import spacy
from typing import List

class SemanticChunker:
    """
    Разбивает текст на семантически целостные фрагменты,
    уважая границы абзацев и предложений.
    """
    def __init__(self, model_name: str = "en_core_web_sm", max_chunk_size: int = 1024, min_chunk_size: int = 100):
        """
        Инициализирует чанкер.
        
        Args:
            model_name (str): Имя модели spaCy для сегментации предложений.
            max_chunk_size (int): Максимальный размер чанка в символах.
            min_chunk_size (int): Минимальный размер чанка для объединения.
        """
        try:
            self.nlp = spacy.load(model_name)
        except OSError:
            print(f"Downloading spaCy model '{model_name}'...")
            spacy.cli.download(model_name)
            self.nlp = spacy.load(model_name)
        
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size

    def chunk(self, text: str) -> List[str]:
        """
        Выполняет семантическое чанкование.
        
        Args:
            text (str): Входной текст.
        
        Returns:
            List[str]: Список текстовых чанков.
        """
        if not text.strip():
            return []

        final_chunks = []
        # 1. Используем генератор для обработки абзацев по одному
        paragraphs = (p.strip() for p in text.split('\n\n'))
        
        for para in paragraphs:
            if not para:
                continue
            
            # Если абзац достаточно мал, он становится одним чанком
            if len(para) <= self.max_chunk_size:
                final_chunks.append(para)
            else:
                # 2. Если абзац слишком длинный, разбиваем на предложения
                doc = self.nlp(para)
                sentences = [sent.text.strip() for sent in doc.sents]
                
                current_chunk_sentences = []
                current_chunk_len = 0
                
                for sent in sentences:
                    sentence_len = len(sent)
                    
                    if current_chunk_len + sentence_len + 1 > self.max_chunk_size:
                        if current_chunk_sentences:
                            final_chunks.append(" ".join(current_chunk_sentences))
                        current_chunk_sentences = [sent]
                        current_chunk_len = sentence_len
                    else:
                        current_chunk_sentences.append(sent)
                        current_chunk_len += sentence_len + 1
                
                # Добавляем последний оставшийся чанк
                if current_chunk_sentences:
                    final_chunks.append(" ".join(current_chunk_sentences))
                    
        return final_chunks