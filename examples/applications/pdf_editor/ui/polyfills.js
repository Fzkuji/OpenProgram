// pdf.js 6 calls a few very new built-ins; fill the ones the host's Chromium may lack.
// This module is imported before pdf.js so the worker code (run in-page) sees them too.
if (typeof Math.sumPrecise !== "function") {
  Math.sumPrecise = function sumPrecise(values) {
    let sum = 0, carry = 0; // Kahan–Babuška summation
    for (const value of values) {
      const next = sum + value;
      carry += Math.abs(sum) >= Math.abs(value) ? (sum - next) + value : (value - next) + sum;
      sum = next;
    }
    return sum + carry;
  };
}
for (const proto of [Map.prototype, WeakMap.prototype]) {
  if (typeof proto.getOrInsert !== "function") {
    proto.getOrInsert = function (key, value) { if (!this.has(key)) this.set(key, value); return this.get(key); };
  }
  if (typeof proto.getOrInsertComputed !== "function") {
    proto.getOrInsertComputed = function (key, make) { if (!this.has(key)) this.set(key, make(key)); return this.get(key); };
  }
}
