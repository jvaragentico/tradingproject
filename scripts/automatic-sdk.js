import {OrderSide} from '@polymarket/client';
import {fetchBalanceAllowance} from '@polymarket/client/actions';
import {units,validateOrder} from '../frontend/wallet-policy.js';

export function createSdkExchange({client,engine,eligible,signGuard=null,readBalance=null,now=()=>Date.now()/1000}) {
  const reviewed=new Map();
  const wallet=String(client.account.wallet).toLowerCase();
  async function balance() {return readBalance ? readBalance() : fetchBalanceAllowance(client,{assetType:'COLLATERAL'});}
  async function startup(state) {
    await eligible();
    const b=await balance();
    if(units(state.config.capital)>BigInt(b.balance)) throw new Error('Session capital exceeds available collateral.');
    for await(const page of client.listOpenOrders()) if(page.items.length) throw new Error('Existing orders must be reconciled before starting.');
    for await(const page of client.listPositions({user:wallet,conditionId:state.conditionId,filterAmount:0}))
      if(page.items.some(p=>!Number.isFinite(Number(p.size)) || Number(p.size)!==0)) throw new Error('Existing market inventory must be reconciled before starting.');
  }
  return {
    startup,
    async preflight(intent,state) {
      await eligible();
      if(state.market.fee_taker_only!==true || state.config.allow_taker) throw new Error('Automatic mode is maker-only.');
      const tokenId=intent.side==='Up'?state.market.up_token:state.market.down_token;
      const book=await client.fetchOrderBook({tokenId});
      if(String(book.assetId)!==tokenId || book.negRisk!==false || book.conditionId!==state.conditionId) throw new Error('Unexpected order book.');
      const market={slug:state.market.slug,endsAt:state.market.end,fetchedAt:now(),outcomes:{
        [intent.side]:{tokenId,tick:book.tickSize,minSize:book.minOrderSize}}};
      const order={slug:market.slug,outcome:intent.side,price:intent.price,size:intent.shares,confirmed:true};
      const checked=validateOrder(order,market,now());
      if(checked.cost>units(state.config.order_dollars) || now()>=market.endsAt-2) throw new Error('Order exceeds session limits.');
      const b=await balance();
      if(BigInt(b.balance)<checked.cost) throw new Error('Insufficient exchange collateral.');
      reviewed.set(intent.localId,{order,market,tokenId});
    },
    async place(intent) {
      const review=reviewed.get(intent.localId);
      if(!review || review.order.price!==intent.price || review.order.size!==intent.shares) throw new Error('Missing reviewed intent.');
      validateOrder(review.order,review.market,now());
      // The CLI signer's before-sign hook repeats state and freshness checks.
      if(signGuard)signGuard.intent=intent;
      try {
        return await client.placeLimitOrder({tokenId:review.tokenId,side:OrderSide.BUY,price:intent.price,
          size:intent.shares,postOnly:true});
      } finally {if(signGuard)signGuard.intent=null;}
    },
    async cancel(orderId) {
      const result=await client.cancelOrder({orderId});
      if(result.canceled?.includes(orderId))return result;
      const order=await client.fetchOrder({orderId});
      if(order.id===orderId && ['CANCELED','CANCELLED'].includes(order.status.toUpperCase()))return {canceled:[orderId]};
      if(order.id===orderId && units(order.sizeMatched)===units(order.originalSize))return {canceled:[orderId],fullyFilled:true};
      return result;
    },
    async fills(orderId) {
      const state=await engine.state();
      const order=await client.fetchOrder({orderId});
      if(order.id!==orderId || order.makerAddress.toLowerCase()!==wallet || order.side!=='BUY' ||
          ![state.market.up_token,state.market.down_token].includes(String(order.assetId)))throw new Error('Order ownership or asset mismatch.');
      const results=[];
      for await(const page of client.listAccountTrades({market:state.conditionId})) {
        for(const trade of page.items) {
          const matches=trade.makerOrders.filter(m=>m.orderId===orderId);
          if(matches.length>1)throw new Error('Ambiguous maker fill.');
          for(const maker of matches) {
            if(maker.makerAddress.toLowerCase()!==wallet || maker.side!=='BUY' || String(maker.assetId)!==String(order.assetId)) throw new Error('Unexpected account fill.');
            results.push({orderId,id:trade.id+':'+trade.bucketIndex,status:trade.status.toUpperCase(),
              shares:maker.matchedAmount,price:maker.price,fee:'0'});
          }
        }
      }
      const observed=results.filter(f=>f.status!=='FAILED').reduce((sum,f)=>sum+units(f.shares),0n);
      if(observed<units(order.sizeMatched))results.push({orderId,id:'awaiting_account_trade',status:'PENDING'});
      return results;
    }
  };
}
