import test from 'node:test';
import assert from 'node:assert/strict';
import {resolveAccountWallet} from '../scripts/account-wallet.js';

const signer='0x8041Cc720aBC7DA28B056439aa2932Dbb879c408';
const funded='0xebfaf15b3f8c38166d61a31ced834b5bc44b8064';

test('uses the public profile account wallet rather than the signer',async()=>{
  const client={fetchPublicProfile:async({address})=>{assert.equal(address,signer);return {wallet:funded};}};
  assert.equal(await resolveAccountWallet(signer,'',client),funded);
});

test('rejects a wallet that conflicts with the public profile',async()=>{
  const client={fetchPublicProfile:async()=>({wallet:funded})};
  await assert.rejects(resolveAccountWallet(signer,signer,client),/differs/);
});

test('lets the SDK derive a new account with no public profile',async()=>{
  const client={fetchPublicProfile:async()=>null};
  assert.equal(await resolveAccountWallet(signer,'',client),undefined);
});
