import test from 'node:test';
import assert from 'node:assert/strict';
import {createSdkExchange} from '../scripts/automatic-sdk.js';
const wallet='0x1111111111111111111111111111111111111111';
const state={conditionId:'condition',market:{slug:'btc-updown-5m-0',end:300,up_token:'up',down_token:'down',fee_taker_only:true},config:{capital:10,order_dollars:3,allow_taker:false}};
const intent={localId:'1',side:'Up',price:'0.44',shares:'5'};
function mock() {
 const posted=[];
 const client={account:{wallet},fetchPortfolioValue:async()=>({wallet,value:'0'}),listOpenOrders:async function*(){yield {items:[]};},listPositions:async function*(){yield {items:[]};},
  fetchOrderBook:async()=>({assetId:'up',conditionId:'condition',negRisk:false,tickSize:'0.01',minOrderSize:'5'}),
  placeLimitOrder:async order=>{posted.push(order);return {ok:true,orderId:'order'};},
  cancelOrder:async()=>({canceled:['order']}),fetchOrder:async()=>({id:'order',makerAddress:wallet,assetId:'up',side:'BUY',sizeMatched:'5',originalSize:'5',status:'MATCHED'}),
  listAccountTrades:async function*(){yield {items:[{id:'trade',conditionId:'condition',bucketIndex:0,status:'TRADE_STATUS_CONFIRMED',makerOrders:[{orderId:'order',makerAddress:wallet,assetId:'up',side:'BUY',matchedAmount:'5',price:'0.44',feeRateBps:'0'}]}]};}};
 return {client,posted};
}
test('SDK reconciliation uses own confirmed maker execution and exact amounts',async()=>{
 const {client}=mock();const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{},now:()=>101});
 assert.deepEqual(await exchange.fills('order'),[{orderId:'order',id:'trade:0',status:'CONFIRMED',shares:'5',price:'0.44',fee:'0'}]);
});
test('missing matched fills return pending marker',async()=>{
 const {client}=mock();client.listAccountTrades=async function*(){yield {items:[]};};
 const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{}});
 assert.equal((await exchange.fills('order'))[0].status,'PENDING');
});

test('overlapping trade pages cannot hide missing account fills',async()=>{
 const {client}=mock();const pages=client.listAccountTrades;
 client.listAccountTrades=async function*(){for await(const page of pages()){yield page;yield structuredClone(page);}};
 client.fetchOrder=async()=>({id:'order',makerAddress:wallet,assetId:'up',side:'BUY',sizeMatched:'10'});
 const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{}});
 const fills=await exchange.fills('order');
 assert.equal(fills.filter(f=>f.status==='CONFIRMED').length,1);
 assert.equal(fills.at(-1).status,'PENDING');
});

test('a match arriving during history pagination holds new submissions',async()=>{
 const {client}=mock();let reads=0;
 client.fetchOrder=async()=>({id:'order',makerAddress:wallet,assetId:'up',side:'BUY',sizeMatched:++reads===1?'5':'8'});
 const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{}});
 assert.equal((await exchange.fills('order')).at(-1).status,'PENDING');
});

test('changed payload across overlapping pages is rejected',async()=>{
 const {client}=mock();const pages=client.listAccountTrades;
 client.listAccountTrades=async function*(){for await(const page of pages()){
   yield page;const changed=structuredClone(page);changed.items[0].makerOrders[0].matchedAmount='4';yield changed;
 }};
 const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{}});
 await assert.rejects(exchange.fills('order'),/changed across pages/);
});

test('duplicate trade confirmation advances status without double counting',async()=>{
 const {client}=mock();const pages=client.listAccountTrades;
 client.listAccountTrades=async function*(){for await(const page of pages()){
   const pending=structuredClone(page);pending.items[0].status='TRADE_STATUS_MINED';yield pending;yield page;
 }};
 const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{}});
 assert.equal((await exchange.fills('order')).length,1);
 assert.equal((await exchange.fills('order'))[0].status,'CONFIRMED');
});
test('ownership mismatch stops reconciliation',async()=>{
 const {client}=mock();client.fetchOrder=async()=>({id:'order',makerAddress:'other',side:'BUY'});
 const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{}});
 await assert.rejects(exchange.fills('order'),/ownership/);
});
test('placement without reviewed preflight is rejected',async()=>{
 const {client,posted}=mock();const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{}});
 await assert.rejects(exchange.place(intent),/reviewed/);assert.equal(posted.length,0);
});
test('preflighted automatic cycle uses post-only SDK BUY and caps',async()=>{
 const {client,posted}=mock();const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{},now:()=>101,readBalance:async()=>({balance:10000000n}),stopFloor:'1'});
 await exchange.startup(state);await exchange.preflight(intent,state);await exchange.place(intent);
 assert.deepEqual(posted,[{tokenId:'up',side:'BUY',price:'0.44',size:'5',postOnly:true}]);
});
test('startup stops on insufficient capital or preexisting orders',async()=>{
 const {client}=mock();const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{},readBalance:async()=>({balance:1n}),stopFloor:'1'});
 await assert.rejects(exchange.startup(state),/capital|stop floor/);
 const funded=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{},readBalance:async()=>({balance:10000000n}),stopFloor:'1'});
 client.listOpenOrders=async function*(){yield {items:[{id:'unrelated'}]};};
 await assert.rejects(funded.startup(state),/Existing orders/);
});

test('BTC-only account floor blocks trading before and after startup',async()=>{
 const {client,posted}=mock();let portfolio='0';
 client.fetchPortfolioValue=async()=>({wallet,value:portfolio});
 const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{},
  now:()=>101,readBalance:async()=>({balance:10000000n}),stopFloor:'50'});
 await assert.rejects(exchange.startup(state),/stop floor/);
 portfolio='51';await exchange.startup(state);portfolio='41';
 await assert.rejects(exchange.preflight(intent,state),/stop floor at risk/);
 assert.equal(posted.length,0);
 const wrong=structuredClone(state);wrong.market.slug='eth-updown-5m-0';
 await assert.rejects(exchange.startup(wrong),/BTC Up\/Down 5m/);
});

test('startup checks normalized currentSize rather than historical size',async()=>{
 const {client}=mock();client.listPositions=async function*(){yield {items:[{conditionId:'condition',currentSize:'5',totalSize:'10'}]};};
 const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{},
  readBalance:async()=>({balance:10000000n}),stopFloor:'1'});
 await assert.rejects(exchange.startup(state),/Existing market inventory/);
 client.listPositions=async function*(){yield {items:[{conditionId:'condition',currentSize:'0',totalSize:'10'}]};};
 await exchange.startup(state);
});

test('unconfirmed official trade status never credits account inventory',async()=>{
 const {client}=mock();const pages=client.listAccountTrades;
 client.listAccountTrades=async function*(){for await(const page of pages()){
  page.items[0].status='TRADE_STATUS_MINED';yield page;
 }};
 const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{}});
 assert.equal((await exchange.fills('order'))[0].status,'PENDING');
});

test('trade reconciliation rejects another market and a nonzero maker fee',async()=>{
 const {client}=mock();const original=client.listAccountTrades;
 client.listAccountTrades=async function*(){for await(const page of original()){
  page.items[0].conditionId='other';yield page;
 }};
 const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{}});
 await assert.rejects(exchange.fills('order'),/market mismatch/);
 client.listAccountTrades=async function*(){for await(const page of original()){
  page.items[0].makerOrders[0].feeRateBps='1';yield page;
 }};
 await assert.rejects(exchange.fills('order'),/maker fee/);
});

test('stop-floor exit submits bounded FOK sells for session-owned BTC inventory only',async()=>{
 const {client}=mock();const sells=[];
 client.listPositions=async function*(){yield {items:[
  {wallet,conditionId:'condition',assetId:'up',currentSize:'5'},
  {wallet,conditionId:'condition',assetId:'down',currentSize:'3'}]};};
 client.estimateMarketPrice=async request=>{assert.equal(request.side,'SELL');assert.equal(request.orderType,'FOK');return .41;};
 client.placeMarketOrder=async request=>{sells.push(request);return {ok:true,orderId:'exit-'+sells.length,status:'matched',tradeIds:['trade-'+sells.length]};};
 client.waitForOrderFillSettlement=async()=>['0xsettled'];
 client.fetchOrder=async({orderId})=>({id:orderId,makerAddress:wallet,assetId:sells[Number(orderId.slice(-1))-1].tokenId,
  side:'SELL',sizeMatched:sells[Number(orderId.slice(-1))-1].shares});
 const engine={state:async()=>state};
 const exchange=createSdkExchange({client,engine,eligible:async()=>{}});
 const exitState=structuredClone(state);exitState.portfolio={paired_shares:'3',residual_up:'2',residual_down:'0'};
 const recorded=[];await exchange.liquidateOwnPositions(exitState,r=>recorded.push(r));
 assert.deepEqual(sells,[
  {tokenId:'up',side:'SELL',shares:'5',minPrice:.41,orderType:'FOK'},
  {tokenId:'down',side:'SELL',shares:'3',minPrice:.41,orderType:'FOK'}]);
 assert.equal(recorded.length,4);
 assert.equal(recorded.at(-1).confirmed,true);
});

test('stop-floor exit refuses inventory not attributable to this session',async()=>{
 const {client}=mock();client.listPositions=async function*(){yield {items:[{wallet,conditionId:'condition',assetId:'up',currentSize:'6'}]};};
 client.placeMarketOrder=async()=>{throw Error('should not sell');};
 const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{}});
 const exitState=structuredClone(state);exitState.portfolio={paired_shares:'0',residual_up:'5',residual_down:'0'};
 await assert.rejects(exchange.liquidateOwnPositions(exitState),/exceeds this session/);
});

test('take-profit sells only confirmed single-side BTC inventory above cost and fee buffer',async()=>{
 const {client}=mock();const sells=[];let quote=.45;
 client.listPositions=async function*(){yield {items:[{wallet,conditionId:'condition',assetId:'up',currentSize:'10'}]};};
 client.estimateMarketPrice=async()=>quote;
 client.placeMarketOrder=async request=>{sells.push(request);return {ok:true,orderId:'exit',status:'matched',tradeIds:['trade']};};
 client.waitForOrderFillSettlement=async()=>['0xsettled'];
 client.fetchOrder=async()=>({id:'exit',makerAddress:wallet,assetId:'up',side:'SELL',sizeMatched:'10'});
 const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{}});
 const held=structuredClone(state);held.portfolio={paired_shares:'0',residual_up:'10',residual_down:'0',total_cost:'4.00'};
 assert.equal(await exchange.profitOpportunity(held),false);
 assert.deepEqual(await exchange.liquidateOwnPositions(held,async()=>{},true),[]);
 quote=.55;
 assert.equal(await exchange.profitOpportunity(held),true);
 const exits=await exchange.liquidateOwnPositions(held,async()=>{},true);
 assert.equal(exits.length,1);assert.equal(exits[0].confirmed,true);
 assert.equal(sells.length,1);assert.equal(sells[0].minPrice,.55);
 held.portfolio.paired_shares='1';
 assert.equal(await exchange.profitOpportunity(held),false);
});
