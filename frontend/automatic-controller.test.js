import test from 'node:test';
import assert from 'node:assert/strict';
import {AutomaticController} from './automatic-controller.js';
function harness() {
 const state={receivedAt:100,maxAge:5,halted:null,intents:[{localId:'1',side:'Up',price:'0.44',shares:'5',cancel:false}]};
 const calls=[];
 const engine={state:async()=>structuredClone(state),acknowledge:async(...args)=>calls.push(['ack',...args]),rejected:async(...args)=>calls.push(['reject',...args]),canceled:async(...args)=>calls.push(['canceled',...args]),fill:async(...args)=>calls.push(['fill',...args])};
 const exchange={preflight:async()=>{},place:async()=>{calls.push(['place']);return {ok:true,orderId:'exchange-1'};},cancel:async id=>{calls.push(['cancel',id]);return {canceled:[id]};},fills:async()=>[]};
 const controller=new AutomaticController({engine,exchange,now:()=>101});
 return {state,calls,engine,exchange,controller};
}
test('automatic order posted once and accepted identity retained',async()=>{
 const h=harness();await h.controller.step();await h.controller.step();
 assert.equal(h.calls.filter(c=>c[0]==='place').length,1);
 assert.deepEqual(h.calls[1],['ack','1','exchange-1']);
});
test('cancellation acknowledgement releases reservation',async()=>{
 const h=harness();await h.controller.step();h.state.intents[0].cancel=true;await h.controller.step();
 assert.ok(h.calls.some(c=>c[0]==='canceled'&&c[1]==='1'));
});
test('stale state halts and cancels only own accepted orders',async()=>{
 const h=harness();await h.controller.step();h.state.receivedAt=1;await h.controller.step();
 assert.equal(h.controller.halted,'stale_execution_state');
 assert.deepEqual(h.calls.filter(c=>c[0]==='cancel'),[['cancel','exchange-1']]);
});
test('a signal cancellation during preflight prevents submission',async()=>{
 const h=harness();h.exchange.preflight=async()=>h.state.intents[0].cancel=true;
 await h.controller.step();assert.equal(h.calls.filter(c=>c[0]==='place').length,0);
});
test('ambiguous submission halts and is never retried',async()=>{
 const h=harness();h.exchange.place=async()=>{h.calls.push(['place']);throw Error('timeout');};
 await h.controller.step();await h.controller.step();
 assert.equal(h.controller.halted,'ambiguous_submission_requires_reconciliation');
 assert.equal(h.calls.filter(c=>c[0]==='place').length,1);
 assert.equal(h.calls.filter(c=>c[0]==='reject').length,0);
});
test('only confirmed account fills advance inventory',async()=>{
 const h=harness();await h.controller.step();h.state.intents=[];
 h.exchange.fills=async()=>[{orderId:'exchange-1',status:'MINED',id:'a'},{orderId:'exchange-1',status:'CONFIRMED',id:'b'}];
 await h.controller.step();assert.equal(h.calls.filter(c=>c[0]==='fill').length,1);
});
test('failed settlement halts and cancels own outstanding orders',async()=>{
 const h=harness();await h.controller.step();h.exchange.fills=async()=>[{orderId:'exchange-1',status:'FAILED'}];
 await h.controller.step();assert.equal(h.controller.halted,'execution_reconciliation_failed');
});
test('unconfirmed fills hold new submissions while allowing cancellations',async()=>{
 const h=harness();await h.controller.step();h.state.intents[0].cancel=true;
 h.state.intents.push({localId:'2',side:'Down',price:'0.45',shares:'5',cancel:false});
 h.exchange.fills=async()=>[{orderId:'exchange-1',status:'MINED',id:'a'}];
 await h.controller.step();assert.equal(h.calls.filter(c=>c[0]==='place').length,1);
 assert.ok(h.calls.some(c=>c[0]==='cancel'));
});
