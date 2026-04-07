from langchain.embeddings.base import Embeddings
import ollama
from concurrent.futures import ThreadPoolExecutor, as_completed


class OllamaEmbeddings(Embeddings):
    def __init__(self, model, max_workers=5):
        self.model = model
        self.max_workers = max_workers

    # =========================
    # ⚡ PARALLEL DOCUMENT EMBEDDINGS
    # =========================
    def embed_documents(self, texts):
        results = [None] * len(texts)

        def worker(i, text):
            try:
                response = ollama.embeddings(
                    model=self.model,
                    prompt=text
                )
                return i, response["embedding"]
            except Exception as e:
                print(f"⚠️ Embedding failed at index {i}: {e}")
                return i, None

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(worker, i, t)
                for i, t in enumerate(texts)
            ]

            for future in as_completed(futures):
                i, emb = future.result()
                results[i] = emb

        return results

    # =========================
    # 🔍 SINGLE QUERY EMBEDDING
    # =========================
    def embed_query(self, text):
        return ollama.embeddings(
            model=self.model,
            prompt=text
        )["embedding"]