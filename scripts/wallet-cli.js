import {createInterface} from 'node:readline';
import {createSecureClient,OrderSide} from '@polymarket/client';
import {privateKey} from '@polymarket/client/viem';
import {fetchBalanceAllowance} from '@polymarket/client/actions';
import {privateKeyToAccount} from 'viem/accounts';
import {polygon} from 'viem/chains';
import {isAddress} from 'viem';
import {validateOrder} from '../frontend/wallet-policy.js';

// Read the key only from the launcher's anonymous stdin pipe. Never log SDK errors
// verbatim: transports and signing errors may include payload or request details.
let secret = '', options = {};
try {
  const lines = [];
  for await (const line of createInterface({input:process.stdin,terminal:false})) {
    if (lines.length >= 2 || line.length > 4096) throw new Error('Invalid input.');
    lines.push(line);
  }
  secret = lines[0]?.trim() || '';
  if (!secret.startsWith('0x')) secret = '0x'+secret;
  if (!/^0x[0-9a-fA-F]{64}$/.test(secret)) throw new Error('Invalid key format.');
  options = JSON.parse(lines[1] || '{}');
  const account = privateKeyToAccount(secret);
  lines.fill('');
  console.log('Signer address: '+account.address);
  if (options.expectedSigner && options.expectedSigner.toLowerCase() !== account.address.toLowerCase()) throw new Error('Signer mismatch.');
  if (options.action === 'address') {
    console.log('Address verified locally. No funds moved and no exchange request sent.');
  } else {
    if (!['balance','orders','buy','cancel'].includes(options.action)) throw new Error('Invalid action.');
    if (options.wallet && !isAddress(options.wallet)) throw new Error('Invalid trading wallet.');
    const json = async url => {const r=await fetch(url,{signal:AbortSignal.timeout(15000)});if(!r.ok)throw new Error('Service unavailable.');return r.json();};
    async function eligible() {const geo=await json('https://polymarket.com/api/geoblock');if(geo.blocked!==false)throw new Error('Trading is restricted at this location.');}
    if (options.action === 'buy') await eligible();
    let review = null;
    const adapter=privateKey(secret,{chain:polygon});
    const signer={...adapter,async signTypedData(payload){const signature=await adapter.signTypedData(payload);if(review)validateOrder(options,review);return signature;}};
    const client=await createSecureClient({signer,wallet:options.wallet || account.address});
    console.log('Trading wallet: '+client.account.wallet);
    if(options.action === 'balance') {
      const b=await fetchBalanceAllowance(client,{assetType:'COLLATERAL'});
      console.log('Trading collateral base units: '+String(b.balance));
      console.log('Collateral decimals: 6. BNB Chain assets require a supported deposit.');
    } else if(options.action === 'orders') {
      for await(const page of client.listOpenOrders()) for(const o of page.items) console.log(JSON.stringify({id:o.id,side:o.side,price:o.price,status:o.status}));
    } else if(options.action === 'cancel') {
      const r=await client.cancelOrder({orderId:options.orderId});
      console.log(JSON.stringify(r));
    } else {
      await eligible();
      review=await json('http://127.0.0.1:8787/api/market?slug='+encodeURIComponent(options.slug));
      const checked=validateOrder(options,review);
      const b=await fetchBalanceAllowance(client,{assetType:'COLLATERAL'});
      if(BigInt(b.balance)<checked.cost)throw new Error('Insufficient trading collateral.');
      validateOrder(options,review);
      const result=await client.placeLimitOrder({tokenId:checked.tokenId,side:OrderSide.BUY,price:options.price,size:options.size,postOnly:true});
      console.log(JSON.stringify({ok:result.ok,orderId:result.orderId,status:result.status,code:result.code}));
      if(!result.ok)process.exitCode=1;
    }
  }
} catch {
  // Intentionally redact exception details; never echo invalid key input.
  console.error('Wallet action failed. Check key format, account wallet, funding, eligibility, market inputs and network connection.');
  process.exitCode=1;
} finally {secret='';}
