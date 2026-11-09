from db_store import DatabaseStore
from memory.short_term import ShortTermMemory

db = DatabaseStore()
try:
    ShortTermMemory(db.pool).clear("tuv")
finally:
    db.close()