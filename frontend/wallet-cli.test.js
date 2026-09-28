import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {join} from 'node:path';

const fixtureKey='0x'+'11'.repeat(32); // Public test fixture; never a funded key.
const fixtureAddress='0x19E7E376E7C213B7E7e7e46cc70A5dD086DAff2A';

function runAddress(expectedSigner) {
 return new Promise((resolve,reject)=>{
  const child=spawn(process.execPath,[join(process.cwd(),'scripts','wallet-cli.js')],{stdio:['pipe','pipe','pipe'],windowsHide:true});
  let output='';
  child.stdout.on('data',chunk=>output+=chunk);
  child.stderr.on('data',chunk=>output+=chunk);
  child.once('error',reject);
  child.once('exit',code=>resolve({code,output}));
  child.stdin.end(fixtureKey+'\n'+JSON.stringify({action:'address',expectedSigner})+'\n');
 });
}

test('local address check reports only the public signer',async()=>{
 const result=await runAddress(fixtureAddress);
 assert.equal(result.code,0);
 assert.match(result.output,/Address verified locally/);
 assert.match(result.output,new RegExp(fixtureAddress,'i'));
 assert.ok(!result.output.includes(fixtureKey));
});

test('signer mismatch reports a safe diagnostic stage',async()=>{
 const result=await runAddress('0x8041Cc720aBC7DA28B056439aa2932Dbb879c408');
 assert.equal(result.code,1);
 assert.match(result.output,/signer verification/);
 assert.match(result.output,/Signer mismatch\./);
 assert.ok(!result.output.includes(fixtureKey));
});
