export function acceptPayment(amount) {
  if (!Number.isFinite(amount) || amount <= 0) throw new Error("invalid amount");
  return { amount };
}
