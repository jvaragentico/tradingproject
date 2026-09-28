import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {existsSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {AutomaticController} from './automatic-controller.js';
import {createSdkExchange} from '../scripts/automatic-sdk.js';

test('controller and Python worker reconcile reversals, partial fills and shutdown over HTTP',async()=>{
 const localPython=join(process.cwd(),'.venv',process.platform==='win32'?'Scripts':'bin',process.platform==='win32'?'python.exe':'python');
 const python=existsSync(localPython)?localPython:'python';
 const root=join(process.cwd(),'runs','integration-'+Date.now());
 const child=spawn(python,['tests/automatic_fixture.py',root],{windowsHide:true,stdio:['pipe','pipe','pipe']});
 const lines=createInterface({input:child.stdout});
 const queue=[],waiters=[];
 lines.on('line',line=>{const value=JSON.parse(line);if(waiters.length)waiters.shift()(value);else queue.push(value);});
 const next=()=>queue.length?Promise.resolve(queue.shift()):new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(Error('Offline fixture timeout')),10000);
   waiters.push(value=>{clearTimeout(timer);resolve(value);});
 });
 child.stderr.resume();
 try {
  const {url}=await next();
  const request=async(path,body)=>{
   const response=await fetch(url+path,{method:body?'POST':'GET',headers:{Authorization:'Bearer offline-integration',
    ...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
   assert.equal(response.status,200);return response.json();
  };
  const update=body=>request('/update',body);
  const engine={state:()=>request('/state'),acknowledge:(local_id,exchange_id)=>update({kind:'execution_ack',local_id,exchange_id}),
   rejected:local_id=>update({kind:'execution_reject',local_id}),canceled:local_id=>update({kind:'execution_cancel',local_id}),
   fill:(local_id,f)=>update({kind:'execution_fill',local_id,fill_id:f.id,shares:f.shares,price:f.price,fee:f.fee,status:f.status})};
  const wallet='0x1111111111111111111111111111111111111111',orders=new Map();
  const client={account:{wallet},fetchPortfolioValue:async()=>({wallet,value:'0'}),listOpenOrders:async function*(){yield {items:[]};},listPositions:async function*(){yield {items:[]};},
   fetchOrderBook:async({tokenId})=>({assetId:tokenId,conditionId:'condition',negRisk:false,tickSize:'0.01',minOrderSize:'5'}),
   placeLimitOrder:async o=>{assert.equal(o.postOnly,true);assert.equal(o.side,'BUY');const id='order-'+orders.size;
    orders.set(id,{id,makerAddress:wallet,assetId:o.tokenId,side:'BUY',originalSize:o.size,sizeMatched:'0',status:'LIVE',price:o.price});return {ok:true,orderId:id};},
   cancelOrder:async({orderId})=>{orders.get(orderId).status='CANCELED';return {canceled:[orderId]};},
   fetchOrder:async({orderId})=>orders.get(orderId),
   listAccountTrades:async function*(){yield {items:[...orders.values()].filter(o=>Number(o.sizeMatched)>0).map(o=>({
    id:'fill-'+o.id,conditionId:'condition',bucketIndex:0,status:'TRADE_STATUS_CONFIRMED',makerOrders:[{orderId:o.id,makerAddress:wallet,assetId:o.assetId,
     side:'BUY',matchedAmount:o.sizeMatched,price:o.price,feeRateBps:'0'}]}))};}};
  const exchange=createSdkExchange({client,engine,eligible:async()=>{},readBalance:async()=>({balance:10000000n}),stopFloor:'1'});
  await exchange.startup(await engine.state());
  const controller=new AutomaticController({engine,exchange});
  await controller.step();assert.equal(controller.halted,null);assert.equal(orders.size,1);
  const up=[...orders.values()][0];assert.equal(up.assetId,'up');up.sizeMatched='5';
  child.stdin.write('reverse\n');assert.equal((await next()).reversed,true);
  await controller.step();assert.equal(controller.halted,null);
  const down=[...orders.values()].find(o=>o.assetId==='down');assert.ok(down);down.sizeMatched='3';
  await controller.step();assert.equal(controller.halted,null);
  let state=await engine.state();
  assert.equal(Number(state.portfolio.paired_shares),3);assert.equal(Number(state.portfolio.residual_up),2);
  assert.equal(Number(state.portfolio.cash),6.45);
  await controller.step();state=await engine.state();assert.equal(Number(state.portfolio.cash),6.45);
  await update({kind:'execution_stop'});assert.deepEqual(await controller.stop(),[]);
  state=await engine.state();assert.equal(state.intents.length,0);
  assert.ok([...orders.values()].every(o=>o.status==='CANCELED'));
 } finally {
  lines.close();
  if(child.exitCode===null)await new Promise(resolve=>{
   const timer=setTimeout(()=>child.kill(),5000);
   child.once('exit',()=>{clearTimeout(timer);resolve();});child.stdin.end();
  });
  // root is constructed inside this workspace's runs directory above.
  rmSync(root,{recursive:true,force:true,maxRetries:10,retryDelay:100});
 }
});

