import {OrderSide,OrderType} from '@polymarket/client';
import {fetchBalanceAllowance} from '@polymarket/client/actions';
import {units,validateOrder} from '../frontend/wallet-policy.js';

export function createSdkExchange({client,engine,eligible,signGuard=null,readBalance=null,now=()=>Date.now()/1000,stopFloor='50'}) {
  const reviewed=new Map();
  const wallet=String(client.account.wallet).toLowerCase();
  const floorUnits=units(stopFloor);
  if(floorUnits<=0n)throw new Error('Invalid account stop floor.');
  async function balance() {return readBalance ? readBalance() : fetchBalanceAllowance(client,{assetType:'COLLATERAL'});}
  async function accountValue() {
    const [cash,positions]=await Promise.all([balance(),client.fetchPortfolioValue({user:wallet})]);
    if(!positions || String(positions.wallet).toLowerCase()!==wallet)throw new Error('Portfolio wallet mismatch.');
    const cashUnits=BigInt(cash.balance),positionUnits=units(positions.value);
    if(cashUnits<0n)throw new Error('Invalid account collateral.');
    return {cashUnits,positionUnits,totalUnits:cashUnits+positionUnits};
  }
  async function startup(state) {
    await eligible();
    if(!/^btc-updown-5m-\d+$/.test(state.market.slug))throw new Error('Automatic trading supports BTC Up/Down 5m only.');
    if((await accountValue()).totalUnits<=floorUnits)throw new Error('Account value is at or below the stop floor.');
    const b=await balance();
    if(units(state.config.capital)>BigInt(b.balance)) throw new Error('Session capital exceeds available collateral.');
    for await(const page of client.listOpenOrders()) if(page.items.length) throw new Error('Existing orders must be reconciled before starting.');
    for await(const page of client.listPositions({user:wallet,conditionId:state.conditionId,filterAmount:0}))
      if(page.items.some(p=>p.conditionId!==state.conditionId || units(p.currentSize)!==0n)) throw new Error('Existing market inventory must be reconciled before starting.');
  }
  return {
    startup,
    accountValue,
    async liquidateOwnPositions(state,onResult=async()=>{}) {
      await eligible();
      if(!/^btc-updown-5m-\d+$/.test(state.market.slug))throw new Error('Only BTC 5m inventory can be exited.');
      const maximum={
        [state.market.up_token]:units(state.portfolio.paired_shares)+units(state.portfolio.residual_up),
        [state.market.down_token]:units(state.portfolio.paired_shares)+units(state.portfolio.residual_down)
      };
      const rows=[];
      for await(const page of client.listPositions({user:wallet,conditionId:state.conditionId,filterAmount:0}))
        rows.push(...page.items);
      const attempted=new Set(),results=[];
      for(const position of rows) {
        const tokenId=String(position.assetId),size=units(position.currentSize);
        if(position.wallet.toLowerCase()!==wallet || position.conditionId!==state.conditionId ||
            !Object.hasOwn(maximum,tokenId))throw new Error('Unexpected account position during exit.');
        if(size===0n)continue;
        if(attempted.has(tokenId) || size>maximum[tokenId])throw new Error('Exit inventory exceeds this session’s confirmed fills.');
        attempted.add(tokenId);
        const shares=position.currentSize;
        const price=await client.estimateMarketPrice({tokenId,side:OrderSide.SELL,shares,orderType:OrderType.FOK});
        if(!Number.isFinite(price) || price<=0 || price>=1)throw new Error('No executable exit price.');
        if(signGuard)signGuard.exit={tokenId,shares,minPrice:price};
        let result;
        try {
          result=await client.placeMarketOrder({tokenId,side:OrderSide.SELL,shares,minPrice:price,orderType:OrderType.FOK});
        } finally {if(signGuard)signGuard.exit=null;}
        const record={tokenId,shares,minPrice:price,ok:result.ok===true,orderId:result.orderId||null,
          status:result.status||null,confirmed:false};
        results.push(record);
        await onResult(record);
        if(!result.ok || !result.orderId)throw new Error('Exit was not accepted; inspect positions.');
        if(result.status!=='matched' || !Array.isArray(result.tradeIds) || !result.tradeIds.length)
          throw new Error('Exit fill is not yet evidenced; inspect positions.');
        const hashes=await client.waitForOrderFillSettlement(result);
        if(!Array.isArray(hashes) || !hashes.length)throw new Error('Exit settlement was not confirmed.');
        const order=await client.fetchOrder({orderId:result.orderId});
        if(order.id!==result.orderId || order.makerAddress.toLowerCase()!==wallet || order.side!=='SELL' ||
            String(order.assetId)!==tokenId || units(order.sizeMatched)!==size)
          throw new Error('Exit order did not fully match this account position.');
        record.confirmed=true;
        await onResult(record);
      }
      return results;
    },
    async preflight(intent,state) {
      await eligible();
      const value=await accountValue();
      if(value.totalUnits<=floorUnits)throw new Error('Account value reached the stop floor.');
      if(state.market.fee_taker_only!==true || state.config.allow_taker) throw new Error('Automatic mode is maker-only.');
      const tokenId=intent.side==='Up'?state.market.up_token:state.market.down_token;
      const book=await client.fetchOrderBook({tokenId});
      if(String(book.assetId)!==tokenId || book.negRisk!==false || book.conditionId!==state.conditionId) throw new Error('Unexpected order book.');
      const market={slug:state.market.slug,endsAt:state.market.end,fetchedAt:now(),outcomes:{
        [intent.side]:{tokenId,tick:book.tickSize,minSize:book.minOrderSize}}};
      const order={slug:market.slug,outcome:intent.side,price:intent.price,size:intent.shares,confirmed:true};
      const checked=validateOrder(order,market,now());
      if(checked.cost>units(state.config.order_dollars) || now()>=market.endsAt-2) throw new Error('Order exceeds session limits.');
      if(checked.cost>value.totalUnits-floorUnits)throw new Error('Order would put the stop floor at risk.');
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
      const unique=new Map();
      for await(const page of client.listAccountTrades({market:state.conditionId})) {
        for(const trade of page.items) {
          if(trade.conditionId!==state.conditionId)throw new Error('Account trade market mismatch.');
          const matches=trade.makerOrders.filter(m=>m.orderId===orderId);
          if(matches.length>1)throw new Error('Ambiguous maker fill.');
          for(const maker of matches) {
            if(maker.makerAddress.toLowerCase()!==wallet || maker.side!=='BUY' || String(maker.assetId)!==String(order.assetId)) throw new Error('Unexpected account fill.');
            if(maker.feeRateBps!=null && units(maker.feeRateBps)!==0n)
              throw new Error('Unexpected maker fee; reconcile the account manually.');
            const status=trade.status==='TRADE_STATUS_CONFIRMED'?'CONFIRMED':
              trade.status==='TRADE_STATUS_FAILED'?'FAILED':'PENDING';
            const fill={orderId,id:trade.id+':'+trade.bucketIndex,status,
              shares:maker.matchedAmount,price:maker.price,fee:'0'};
            const previous=unique.get(fill.id);
            if(previous && (units(previous.shares)!==units(fill.shares) || units(previous.price)!==units(fill.price)))
              throw new Error('Account fill changed across pages.');
            // Pagination may overlap while the trade progresses to confirmation.
            // Never count a repeated trade twice or discard a failed settlement.
            if(!previous || fill.status==='FAILED' ||
                (previous.status!=='FAILED' && (fill.status==='CONFIRMED' || previous.status!=='CONFIRMED')))
              unique.set(fill.id,fill);
          }
        }
      }
      const results=[...unique.values()];
      // A match may arrive while the paginated trade history is being read.
      const latest=await client.fetchOrder({orderId});
      if(latest.id!==orderId || latest.makerAddress.toLowerCase()!==wallet || latest.side!=='BUY' ||
          String(latest.assetId)!==String(order.assetId))throw new Error('Order identity changed during reconciliation.');
      const observed=results.filter(f=>f.status!=='FAILED').reduce((sum,f)=>sum+units(f.shares),0n);
      if(observed<units(latest.sizeMatched))results.push({orderId,id:'awaiting_account_trade',status:'PENDING'});
      return results;
    }
  };
}
