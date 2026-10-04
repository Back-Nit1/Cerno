function subtotal(items) {
  return items.reduce((sum, item) => sum + item.price * item.quantity, 0);
}

function memberDiscount(amount, member) {
  return member ? amount * 0.1 : 0;
}

export function total(order) {
  const amount = subtotal(order.items);
  return Math.round((amount - memberDiscount(amount, order.member)) * 100) / 100;
}
