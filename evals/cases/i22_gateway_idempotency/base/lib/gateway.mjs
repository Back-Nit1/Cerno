export function paymentHeaders(key) {
  return { "Idempotency-Key": key };
}
