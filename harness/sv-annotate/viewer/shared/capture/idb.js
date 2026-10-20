// Minimal IndexedDB append-only event buffer. One object store per session key.
// Buffer is cleared at session start (no cross-reload resume in Phase-0); survives network loss.
const DB_NAME = 'sv_capture';
const STORE = 'events';

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'seq', autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export function openBuffer(sessionKey) {
  const dbP = openDb();
  const tag = sessionKey;
  return {
    async append(event) {
      const db = await dbP;
      return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).add({ tag, event });
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
      });
    },
    async readAll() {
      const db = await dbP;
      return new Promise((resolve, reject) => {
        const out = [];
        const tx = db.transaction(STORE, 'readonly');
        const cursor = tx.objectStore(STORE).openCursor();
        cursor.onsuccess = () => {
          const c = cursor.result;
          if (c) { if (c.value.tag === tag) out.push(c.value.event); c.continue(); }
          else resolve(out);
        };
        cursor.onerror = () => reject(cursor.error);
      });
    },
    async clear() {
      const db = await dbP;
      return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite');
        const cursor = tx.objectStore(STORE).openCursor();
        cursor.onsuccess = () => {
          const c = cursor.result;
          if (c) { if (c.value.tag === tag) c.delete(); c.continue(); }
          else resolve();
        };
        cursor.onerror = () => reject(cursor.error);
      });
    },
  };
}
