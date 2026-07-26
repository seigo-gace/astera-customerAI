import { Worker } from "node:worker_threads";

export class WorkerPool {
  constructor({ size = 2, timeoutMs = 10_000 } = {}) {
    this.size = Math.max(1, Math.min(4, Number(size) || 2));
    this.timeoutMs = Math.max(1000, Number(timeoutMs) || 10_000);
    this.nextId = 1;
    this.queue = [];
    this.workers = [];
    this.shuttingDown = false;
    for (let index = 0; index < this.size; index += 1) this.#spawn();
  }

  #spawn() {
    const worker = new Worker(new URL("./workers/analysis-worker.mjs", import.meta.url));
    const slot = { worker, busy: false, current: null, dead: false };
    worker.on("message", (message) => this.#onMessage(slot, message));
    worker.on("error", (error) => this.#onCrash(slot, error));
    worker.on("exit", (code) => {
      if (!this.shuttingDown && code !== 0) this.#onCrash(slot, new Error(`worker_exit_${code}`));
    });
    this.workers.push(slot);
  }

  #finish(slot) {
    if (slot.current?.timer) clearTimeout(slot.current.timer);
    slot.busy = false;
    slot.current = null;
    this.#drain();
  }

  #onMessage(slot, message) {
    const current = slot.current;
    if (!current || message.id !== current.id) return;
    if (message.ok) current.resolve(message.result);
    else current.reject(new Error(message.error || "worker_failed"));
    this.#finish(slot);
  }

  #onCrash(slot, error) {
    if (this.shuttingDown || slot.dead) return;
    slot.dead = true;
    const index = this.workers.indexOf(slot);
    if (index >= 0) this.workers.splice(index, 1);
    if (slot.current) {
      if (slot.current.timer) clearTimeout(slot.current.timer);
      const current = slot.current;
      slot.current = null;
      if (!current.retried) this.queue.unshift({ ...current, retried: true, timer: null });
      else current.reject(error);
    }
    slot.busy = false;
    void slot.worker.terminate().catch(() => {});
    this.#spawn();
    this.#drain();
  }

  exec(workerName, payload) {
    if (this.shuttingDown) return Promise.reject(new Error("worker_pool_shutting_down"));
    return new Promise((resolve, reject) => {
      this.queue.push({ id: this.nextId++, workerName, payload, resolve, reject, timer: null, retried: false });
      this.#drain();
    });
  }

  #drain() {
    for (const slot of this.workers) {
      if (!this.queue.length) break;
      if (slot.busy || slot.dead) continue;
      const job = this.queue.shift();
      slot.busy = true;
      slot.current = job;
      job.timer = setTimeout(() => this.#onCrash(slot, new Error(`worker_timeout:${job.workerName}`)), this.timeoutMs);
      slot.worker.postMessage({ id: job.id, workerName: job.workerName, payload: job.payload });
    }
  }

  status() {
    return { size: this.size, workers: this.workers.length, busy: this.workers.filter((slot) => slot.busy).length, queued: this.queue.length };
  }

  async destroy() {
    this.shuttingDown = true;
    while (this.queue.length) this.queue.shift().reject(new Error("worker_pool_shutting_down"));
    await Promise.allSettled(this.workers.map((slot) => slot.worker.terminate()));
    this.workers = [];
  }
}
