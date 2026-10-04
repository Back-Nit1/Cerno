export function total(order) {
  let subtotal = 0;
  for (const item of order.items) subtotal += item.price * item.quantity;
  const discount = order.member ? subtotal * 0.1 : 0;
  return Math.round((subtotal - discount) * 100) / 100;
}
