"use strict";

class FakeSnapshot {
  constructor(ref, value) {
    this.ref = ref;
    this.exists = value !== undefined;
    this._value = value;
  }

  data() {
    return this._value === undefined ? undefined : structuredClone(this._value);
  }
}

class FakeDocumentReference {
  constructor(db, collectionName, id) {
    this.db = db;
    this.collectionName = collectionName;
    this.id = id;
    this.path = `${collectionName}/${id}`;
  }

  async get() {
    return new FakeSnapshot(this, this.db._docs.get(this.path));
  }

  // 트랜잭션 밖 단건 쓰기(실서버의 DocumentReference.set 대응).
  async set(value, options = {}) {
    const current = this.db._docs.get(this.path);
    if (options.merge && current !== undefined) {
      this.db._docs.set(this.path, { ...current, ...structuredClone(value) });
    } else {
      this.db._docs.set(this.path, structuredClone(value));
    }
  }
}

class FakeCollectionReference {
  constructor(db, name) {
    this.db = db;
    this.name = name;
  }

  doc(id) {
    return new FakeDocumentReference(this.db, this.name, id);
  }
}

class FakeTransaction {
  constructor(db) {
    this.db = db;
    this.writes = [];
  }

  async get(ref) {
    return new FakeSnapshot(ref, this.db._docs.get(ref.path));
  }

  create(ref, value) {
    this.writes.push({ type: "create", ref, value: structuredClone(value) });
  }

  update(ref, value) {
    this.writes.push({ type: "update", ref, value: structuredClone(value) });
  }

  commit() {
    for (const write of this.writes) {
      if (write.type === "create" && this.db._docs.has(write.ref.path)) {
        const error = new Error(`already exists: ${write.ref.path}`);
        error.code = 6;
        throw error;
      }
    }
    for (const write of this.writes) {
      if (write.type === "create") {
        this.db._docs.set(write.ref.path, write.value);
      } else {
        const current = this.db._docs.get(write.ref.path);
        if (current === undefined) throw new Error(`missing: ${write.ref.path}`);
        this.db._docs.set(write.ref.path, { ...current, ...write.value });
      }
    }
  }
}

class FakeFirestore {
  constructor(initial = {}) {
    this._docs = new Map(
      Object.entries(initial).map(([path, value]) => [path, structuredClone(value)])
    );
    this._transactionTail = Promise.resolve();
  }

  collection(name) {
    return new FakeCollectionReference(this, name);
  }

  runTransaction(callback) {
    const run = async () => {
      const tx = new FakeTransaction(this);
      const result = await callback(tx);
      tx.commit();
      return result;
    };
    const result = this._transactionTail.then(run, run);
    this._transactionTail = result.then(() => undefined, () => undefined);
    return result;
  }

  read(path) {
    const value = this._docs.get(path);
    return value === undefined ? undefined : structuredClone(value);
  }

  count(collectionName) {
    const prefix = `${collectionName}/`;
    return [...this._docs.keys()].filter((path) => path.startsWith(prefix)).length;
  }
}

module.exports = { FakeFirestore };
