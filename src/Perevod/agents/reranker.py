from typing import List, Dict


class Reranker:
    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2", model=None):
        """Инициализирует модель для переранжирования."""
        self.model_name = model_name
        self.model = model

    def _get_model(self):
        if self.model is None:
            try:
                from sentence_transformers.cross_encoder import CrossEncoder
            except ImportError as e:
                raise RuntimeError(
                    "Reranker requires the optional dependency "
                    "'sentence-transformers'. Install Perevod with the "
                    "'reranker' extra or disable enable_reranker."
                ) from e

            self.model = CrossEncoder(self.model_name)
        return self.model

    def rerank(self, query: str, documents: List[Dict]) -> List[Dict]:
        """
        Переранжирует список документов на основе их релевантности запросу.

        Args:
            query: Строка запроса.
            documents: Список словарей документов, извлеченных на первом этапе.
                       Каждый словарь должен иметь ключ 'text'.

        Returns:
            Отсортированный список словарей документов.
        """
        if not documents:
            return []

        # Формируем пары [запрос, текст документа] для модели
        pairs = [[query, doc["text"]] for doc in documents]

        # Получаем оценки релевантности от Cross-Encoder
        scores = self._get_model().predict(pairs, show_progress_bar=False)

        # Добавляем оценку к каждому документу
        for doc, score in zip(documents, scores):
            doc["rerank_score"] = score

        # Сортируем документы в порядке убывания оценки
        reranked_docs = sorted(documents, key=lambda x: x["rerank_score"], reverse=True)

        return reranked_docs
