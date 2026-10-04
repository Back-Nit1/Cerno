export function acceptPayment(amount) {
  if (!Number.isFinite(amount)) throw new Error("invalid amount");
  return { amount };
}
