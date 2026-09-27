import test from 'node:test';
import assert from 'node:assert/strict';
import {units,validateOrder} from './wallet-policy.js';
const market = {slug:'btc-updown-5m-1',fetchedAt:100,endsAt:300,outcomes:{Up:{tokenId:'up',tick:'0.01',minSize:'5'}}};
const order = {slug:market.slug,outcome:'Up',price:'0.45',size:'5',confirmed:true};
test('valid review maps exact token and cost',()=>assert.deepEqual(validateOrder(order,market,101),{tokenId:'up',cost:2250000n}));
test('no rounding below purchase cap',()=>{
 assert.equal(units('23.590001'),23590001n);
 assert.throws(()=>validateOrder({...order,price:'0.99',size:'23.83'},market,101),/23.59/);
});
test('rejects unreviewed, expired, stale, wrong market and outcome',()=>{
 for(const change of [{confirmed:false},{slug:'other'},{outcome:'Down'}]) assert.throws(()=>validateOrder({...order,...change},market,101));
 assert.throws(()=>validateOrder(order,market,111),/expired/);
 assert.throws(()=>validateOrder(order,{...market,endsAt:101},101),/expired/);
});
test('rejects invalid size, tick and nondecimal input',()=>{
 for(const change of [{size:'4.99'},{size:'5.001'},{price:'0.455'},{price:'NaN'},{price:'1'},{price:'0'},{size:'1e4'}]) assert.throws(()=>validateOrder({...order,...change},market,101));
});
