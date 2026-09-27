import test from 'node:test';
import assert from 'node:assert/strict';
import {createSdkExchange} from '../scripts/automatic-sdk.js';
const wallet='0x1111111111111111111111111111111111111111';
const state={conditionId:'condition',market:{slug:'btc-updown-5m-0',end:300,up_token:'up',down_token:'down',fee_taker_only:true},config:{capital:10,order_dollars:3,allow_taker:false}};
const intent={localId:'1',side:'Up',price:'0.44',shares:'5'};
function mock() {
 const posted=[];
 const client={account:{wallet},listOpenOrders:async function*(){yield {items:[]};},listPositions:async function*(){yield {items:[]};},
  fetchOrderBook:async()=>({assetId:'up',conditionId:'condition',negRisk:false,tickSize:'0.01',minOrderSize:'5'}),
  placeLimitOrder:async order=>{posted.push(order);return {ok:true,orderId:'order'};},
  cancelOrder:async()=>({canceled:['order']}),fetchOrder:async()=>({id:'order',makerAddress:wallet,assetId:'up',side:'BUY',sizeMatched:'5',originalSize:'5',status:'MATCHED'}),
  listAccountTrades:async function*(){yield {items:[{id:'trade',bucketIndex:0,status:'CONFIRMED',makerOrders:[{orderId:'order',makerAddress:wallet,assetId:'up',side:'BUY',matchedAmount:'5',price:'0.44'}]}]};}};
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
   const pending=structuredClone(page);pending.items[0].status='MINED';yield pending;yield page;
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
 const {client,posted}=mock();const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{},now:()=>101,readBalance:async()=>({balance:10000000n})});
 await exchange.startup(state);await exchange.preflight(intent,state);await exchange.place(intent);
 assert.deepEqual(posted,[{tokenId:'up',side:'BUY',price:'0.44',size:'5',postOnly:true}]);
});
test('startup stops on insufficient capital or preexisting orders',async()=>{
 const {client}=mock();const exchange=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{},readBalance:async()=>({balance:1n})});
 await assert.rejects(exchange.startup(state),/capital/);
 const funded=createSdkExchange({client,engine:{state:async()=>state},eligible:async()=>{},readBalance:async()=>({balance:10000000n})});
 client.listOpenOrders=async function*(){yield {items:[{id:'unrelated'}]};};
 await assert.rejects(funded.startup(state),/Existing orders/);
});
