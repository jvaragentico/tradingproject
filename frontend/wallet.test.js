import test from 'node:test';
import assert from 'node:assert/strict';
import {connectWallet} from './wallet.js';
const address='0x1111111111111111111111111111111111111111';
function provider(chain='0x89') {
  const listeners={};
  return {isMetaMask:true,request:async ({method})=>method==='eth_chainId'?chain:[address],on:(name,cb)=>listeners[name]=cb,
    removeListener:(name)=>delete listeners[name],listeners};
}
test('missing extension has actionable error',async()=>{
 globalThis.window={}; await assert.rejects(connectWallet(),/MetaMask is unavailable/);
});
test('BNB account can connect but cannot send Polygon order',async()=>{
 globalThis.window={ethereum:provider('0x38')}; const wallet=await connectWallet();
 assert.equal(wallet.address,address); await assert.rejects(wallet.place({}),/Switch MetaMask to Polygon/); wallet.disconnect();
});
test('changed account invalidates authenticated actions',async()=>{
 const injected=provider();globalThis.window={ethereum:injected};const wallet=await connectWallet();
 injected.listeners.accountsChanged();await assert.rejects(wallet.openOrders(),/changed/);wallet.disconnect();
 assert.deepEqual(injected.listeners,{});
});
test('geoblock fails before authentication or signing',async()=>{
 globalThis.window={ethereum:provider()};const wallet=await connectWallet();
 const previous=globalThis.fetch;globalThis.fetch=async()=>({ok:true,json:async()=>({blocked:true})});
 try{await assert.rejects(wallet.place({}),/restricts trading/);}finally{globalThis.fetch=previous;wallet.disconnect();}
});
