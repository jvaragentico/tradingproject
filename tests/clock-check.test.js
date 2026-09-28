import test from 'node:test';
import assert from 'node:assert/strict';
import {clockOffset} from '../scripts/clock-check.js';

test('accepts a clock within the bounded Polymarket time difference',()=>{
  assert.equal(clockOffset(100000,100200,new Date(100000).toUTCString()),-100);
});

test('rejects a clock that lags the market feed by four seconds',()=>{
  assert.throws(()=>clockOffset(100000,100200,new Date(104000).toUTCString()),/Sync Windows Date & time/);
});

test('rejects slow responses and missing server time',()=>{
  assert.throws(()=>clockOffset(100000,104000,new Date(104000).toUTCString()),/Could not verify/);
  assert.throws(()=>clockOffset(100000,100200,null),/Could not verify/);
});
