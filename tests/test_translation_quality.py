from Perevod.utils.translation_quality import evaluate_translation_sanity, merge_severity


def test_translation_sanity_rejects_empty_output():
    result = evaluate_translation_sanity("Original text.", "")

    assert result.pass_check is False
    assert result.severity == "critical"
    assert result.score_cap == 0.0
    assert "empty" in result.blocking_issues[0].lower()


def test_translation_sanity_flags_truncated_long_translation():
    original = " ".join(["The cultivator crossed the valley."] * 20)
    translated = "Культиватор шел."

    result = evaluate_translation_sanity(original, translated)

    assert result.pass_check is False
    assert result.severity == "high"
    assert result.score_cap <= 4.0


def test_translation_sanity_flags_short_untranslated_latin_output():
    text = "The cultivator entered the ancient hall."

    result = evaluate_translation_sanity(text, text)

    assert result.pass_check is False
    assert any("Latin-script" in issue for issue in result.blocking_issues)


def test_translation_sanity_flags_missing_required_dictionary_terms():
    original = "The Spirit Lotus opened near Dawnkeep."
    translated = "Лотос раскрылся возле крепости."

    result = evaluate_translation_sanity(
        original,
        translated,
        {"Spirit Lotus": "Духовный лотос", "Dawnkeep": "Рассветная крепость"},
    )

    assert result.pass_check is False
    assert "Spirit Lotus -> Духовный лотос" in result.blocking_issues[0]
    assert "Dawnkeep -> Рассветная крепость" in result.blocking_issues[0]


def test_translation_sanity_avoids_substring_dictionary_false_positives():
    original = "The equipment mentioned a Daomark seal."
    translated = "Снаряжение упоминало печать."

    result = evaluate_translation_sanity(
        original,
        translated,
        {"He": "Он", "Dao": "Дао"},
    )

    assert result.pass_check is True
    assert result.blocking_issues == []


def test_translation_sanity_treats_ai_slop_as_suggestion():
    result = evaluate_translation_sanity(
        "He entered the hall.",
        "Он вошел в зал. Стоит отметить, что зал был пуст.",
    )

    assert result.pass_check is True
    assert result.blocking_issues == []
    assert result.suggestions
    assert result.score_cap == 8.0


def test_merge_severity_keeps_higher_severity():
    assert merge_severity("low", "high") == "high"
    assert merge_severity("critical", "medium") == "critical"


def test_translation_sanity_accepts_declined_dictionary_terms():
    original = "The Spirit Lotus opened near Dawnkeep on Thunder Island."
    translated = "Духовного лотоса не оказалось возле Рассветной крепости на Острове Грома."

    result = evaluate_translation_sanity(
        original,
        translated,
        {
            "Spirit Lotus": "Духовный лотос",
            "Dawnkeep": "Рассветная крепость",
            "Thunder Island": "Остров Грома"
        },
    )

    assert result.pass_check is True
    assert result.blocking_issues == []


def test_translation_sanity_accepts_synonyms_and_alternative_variants():
    original = "Cyan Spiritual Liquid was found inside the Cave Mansion Spirit Field."
    translated = "Лазурная духовная жидкость была обнаружена внутри Духовного поля Пещерной обители."

    result = evaluate_translation_sanity(
        original,
        translated,
        {
            "Cyan Spiritual Liquid": "Циановая духовная жидкость / лазурная духовная жидкость",
            "Cave Mansion Spirit Field": "Духовное поле Пещерного особняка; Духовное поле Пещерной обители",
        },
    )

    assert result.pass_check is True
    assert result.blocking_issues == []


def test_translation_sanity_populates_style_metrics_dict():
    result = evaluate_translation_sanity(
        "The cultivator walked along the path through the forest to the cave.",
        "Культиватор шел по тропе через лес к пещере.",
    )

    assert "style_metrics" in result.__dataclass_fields__
    assert "narrative_dash_per_1k" in result.style_metrics
    assert "gerund_per_1k" in result.style_metrics
    assert result.style_metrics["word_count"] >= 4


def test_translation_sanity_blocks_excessive_narrative_em_dashes():
    original = "The cultivator walked. The wind blew. The rain fell. " * 10
    # Нарративные тире как замена запятой — фирменный ИИ-маркер.
    translated = (
        "Лу Сюань шел по тропе — дорога была долгой. "
        "Ветер дул с гор — холодный и резкий. "
        "Дождь начался внезапно — крупные капли били по лицу. "
        "Лес шумел вокруг — листва дрожала от ветра. "
        "Тропа поднималась в гору — камни скользили под ногами. "
        "Пещера была близко — вход темнел впереди. "
    ) * 3

    result = evaluate_translation_sanity(original, translated)

    assert result.pass_check is False
    assert any("em-dash" in issue.lower() for issue in result.blocking_issues)
    assert result.style_metrics["narrative_dash_per_1k"] > 5.0


def test_translation_sanity_blocks_stacked_gerunds():
    original = "He focused on the item. He looked around. He continued walking. " * 10
    # Стек деепричастных оборотов — главный ИИ-маркер русской прозы.
    translated = (
        "Сосредоточив внимание на предмете, Лу Сюань увидел детали. "
        "Осмотревшись по сторонам, он заметил врага. "
        "Взяв сумку в руки, он продолжил путь вперёд. "
        "Завершив проверку формации, мастер расслабился. "
        "Покинув пещеру, культиватор пошёл по тропе. "
        "Увидев результат, Лу Сюань улыбнулся. "
    ) * 3

    result = evaluate_translation_sanity(original, translated)

    assert result.pass_check is False
    assert any("gerund" in issue.lower() for issue in result.blocking_issues)
    assert result.style_metrics["gerund_per_1k"] > 4.0


def test_translation_sanity_accepts_clean_prose_without_style_markers():
    original = "The cultivator walked along the path. " * 20
    # Чистая проза: конечные глаголы, без тире-замен, без коннекторов.
    translated = (
        "Лу Сюань шёл по тропе и осматривал окрестности. "
        "Он заметил редкое растение и остановился. "
        "Мастер достал инструмент и проверил состояние ростка. "
        "Растение выглядело здоровым и сильным. "
        "Он полил его духовной жидкостью и пошёл дальше. "
    ) * 6

    result = evaluate_translation_sanity(original, translated)

    assert result.pass_check is True
    assert result.style_metrics["narrative_dash_per_1k"] <= 3.0
    assert result.style_metrics["gerund_per_1k"] <= 2.5

