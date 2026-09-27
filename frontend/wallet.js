import {createSecureClient, OrderSide} from '@polymarket/client';
import {fetchBalanceAllowance} from '@polymarket/client/actions';
import {signerFrom} from '@polymarket/client/viem';
import {createWalletClient, custom, isAddress} from 'viem';
import {polygon} from 'viem/chains';
import {validateOrder} from './wallet-policy.js';

async function read(path) {
  const response = await fetch(path);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Public market check failed.');
  return data;
}
async function eligible() {
  const geo = await read('/api/geoblock');
  if (geo.blocked !== false) throw new Error('Polymarket restricts trading from this location. No new order will be sent.');
}
export async function connectWallet(accountWallet = '') {
  const provider = window.ethereum?.providers?.find(p => p.isMetaMask) || window.ethereum;
  if (!provider?.request) throw new Error('MetaMask is unavailable. Open this dashboard in a browser with MetaMask installed.');
  if (accountWallet && !isAddress(accountWallet)) throw new Error('Invalid Polymarket trading wallet address.');
  const [address] = await provider.request({method:'eth_requestAccounts'});
  if (!address) throw new Error('No wallet account selected.');
  let valid = true, client = null, authenticating = null, pendingReview = null;
  const invalidate = () => {valid = false; client = null;};
  provider.on?.('accountsChanged', invalidate);
  provider.on?.('chainChanged', invalidate);
  async function check() {
    const accounts = await provider.request({method:'eth_accounts'});
    if (!valid || accounts[0]?.toLowerCase() !== address.toLowerCase()) throw new Error('Wallet account or network changed. Disconnect and reconnect.');
    if (Number(await provider.request({method:'eth_chainId'})) !== 137) throw new Error('Switch MetaMask to Polygon (chain 137), then reconnect. BNB Chain funds need a reviewed deposit first.');
  }
  async function secure() {
    await check();
    if (!client) {
      const adapter = signerFrom(createWalletClient({account:address, chain:polygon, transport:custom(provider)}));
      const guardedSigner = {...adapter, async signTypedData(payload) {
        await check();
        const signature = await adapter.signTypedData(payload);
        await check();
        if (pendingReview) validateOrder(pendingReview.order, pendingReview.market);
        return signature;
      }};
      authenticating ||= createSecureClient({wallet:accountWallet || address, signer:guardedSigner});
      try {const authenticated = await authenticating; await check(); client = authenticated;}
      finally {authenticating = null;}
    }
    return client;
  }
  function cancellation(result) {
    if (Object.keys(result.notCanceled || {}).length) throw new Error('Some orders were not canceled: '+JSON.stringify(result.notCanceled));
    return result;
  }
  return {
    address,
    disconnect() {invalidate(); provider.removeListener?.('accountsChanged',invalidate); provider.removeListener?.('chainChanged',invalidate);},
    async openOrders() {const c=await secure(), orders=[]; for await (const page of c.listOpenOrders()) orders.push(...page.items); return orders;},
    async cancel(orderId) {return cancellation(await (await secure()).cancelOrder({orderId}));},
    async cancelAll() {return cancellation(await (await secure()).cancelAll());},
    async place(order) {
      await check(); await eligible();
      let market = await read('/api/market?slug='+encodeURIComponent(order.slug));
      validateOrder(order, market);
      const c = await secure();
      // Authentication can wait on the extension. Recheck market and eligibility afterwards.
      await check(); await eligible();
      market = await read('/api/market?slug='+encodeURIComponent(order.slug));
      const reviewed = validateOrder(order, market);
      const balance = await fetchBalanceAllowance(c,{assetType:'COLLATERAL'});
      if (BigInt(balance.balance) < reviewed.cost) throw new Error('Insufficient Polymarket trading collateral. Complete a supported deposit first.');
      await check(); validateOrder(order, market);
      if (pendingReview) throw new Error('Wait for the pending order before placing another.');
      pendingReview = {order,market};
      try {
        const result = await c.placeLimitOrder({tokenId:reviewed.tokenId,side:OrderSide.BUY,
          price:String(order.price),size:String(order.size),postOnly:true});
        if (!result.ok) throw new Error(result.message || result.code || 'Exchange rejected the order.');
        return result;
      } finally {pendingReview = null;}
    }
  };
}
