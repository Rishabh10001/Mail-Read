from langchain_chroma import Chroma
from classes.ollama_embedding import OllamaEmbeddings
from chromadb.config import Settings

# =========================
# 🧠 EMBEDDING MODEL
# =========================
embedding_model = OllamaEmbeddings(
    model="nomic-embed-text",
    max_workers=2   # ⚡ use your optimized version
)

# =========================
# 🗄️ SINGLETON DB INSTANCE
# =========================
_db = None


def get_db():
    global _db

    if _db is None:
        _db = Chroma(
            collection_name="emails",
            embedding_function=embedding_model,
            persist_directory="chroma_db",
            client_settings=Settings(
                anonymized_telemetry=False
            )
        )

    return _db