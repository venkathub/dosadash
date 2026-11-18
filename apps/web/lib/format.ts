/** One price format everywhere: ₹60, ₹62.50 — never ₹60.00 beside ₹60. */
export function rupee(value: string | number): string {
  const n = typeof value === "number" ? value : parseFloat(value);
  if (!Number.isFinite(n)) return "₹0";
  return `₹${Number.isInteger(n) ? n.toFixed(0) : n.toFixed(2)}`;
}
