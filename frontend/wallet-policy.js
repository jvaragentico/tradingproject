// Exact decimal arithmetic for review limits; no private keys or credentials.
export function units(value) {
  if (!/^\d+(\.\d{1,6})?$/.test(String(value))) throw new Error('Use a positive decimal with at most six places.');
  const [whole, fraction = ''] = String(value).split('.');
  return BigInt(whole) * 1000000n + BigInt(fraction.padEnd(6, '0'));
}
export function validateOrder(order, market, now = Date.now() / 1000) {
  if (!order.confirmed) throw new Error('Review and acknowledge the real-money order.');
  if (!market || market.slug !== order.slug || now >= market.endsAt || now - market.fetchedAt > 10) throw new Error('Market data is expired. Refresh before signing.');
  const outcome = market.outcomes[order.outcome];
  if (!outcome) throw new Error('Choose Up or Down.');
  const price = units(order.price), size = units(order.size);
  if (price <= 0n || price >= 1000000n || size <= 0n) throw new Error('Invalid price or shares.');
  if (price % units(outcome.tick) || size % 10000n) throw new Error('Price must match the market tick; shares allow two decimals.');
  if (size < units(outcome.minSize)) throw new Error('Order is below the market minimum size.');
  if (price * size > 23590000n * 1000000n) throw new Error('Maximum purchase cost is $23.59 per order.');
  return { tokenId: outcome.tokenId, cost: (price * size + 999999n) / 1000000n };
}
