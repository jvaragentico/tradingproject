import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {existsSync} from 'node:fs';
import {join} from 'node:path';
import {AutomaticController} from '../frontend/automatic-controller.js';
import {createSdkExchange} from './automatic-sdk.js';

export async function runAutomatic({client,options,eligible,signGuard}) {
  if(options.confirmed!==true)throw new Error('Automatic execution requires explicit local confirmation.');
  const capital=String(options.maxSpend);
  if(!Number.isFinite(Number(capital)) || Number(capital)<=0 || !Number.isFinite(Number(options.maxLoss)) ||
      Number(options.maxLoss)<=0 || Number(options.maxLoss)>Number(capital) || !Number.isFinite(Number(options.seconds)) ||
      Number(options.seconds)<5 || Number(options.seconds)>900 || !Number.isFinite(Number(options.orderDollars)) ||
      Number(options.orderDollars)<=0 || Number(options.orderDollars)>Math.min(23.59,Number(capital)))throw new Error('Invalid automatic execution caps.');
  await eligible();
  const python=join(process.cwd(),'.venv',process.platform==='win32'?'Scripts':'bin',process.platform==='win32'?'python.exe':'python');
  if(!existsSync(python))throw new Error('Install the project virtual environment first.');
  const child=spawn(python,['-m','polymarket_bot.automatic_worker'],{cwd:process.cwd(),stdio:['pipe','pipe','pipe'],windowsHide:true});
  const ready=new Promise((resolve,reject)=>{
    const lines=createInterface({input:child.stdout});
    const timer=setTimeout(()=>reject(new Error('Worker startup timed out.')),25000);
    lines.once('line',line=>{clearTimeout(timer);try{resolve(JSON.parse(line));}catch{reject(new Error('Invalid worker response.'));}});
    child.once('error',()=>{clearTimeout(timer);reject(new Error('Worker process failed.'));});
    child.once('exit',()=>{clearTimeout(timer);reject(new Error('Worker exited.'));});
  });
  // The child receives public settings only, never the private key or SDK credentials.
  child.stdin.end(JSON.stringify({...options,capital})+'\n');
  child.stderr.resume(); // Never echo transport diagnostics or sensitive payloads.
  let controller,engine,stopping=false;
  const stoppingSignal=()=>{stopping=true;};
  process.on('SIGINT',stoppingSignal);process.on('SIGTERM',stoppingSignal);
  try {
    const info=await ready;
    if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(info.url) || typeof info.token!=='string')throw new Error('Invalid worker endpoint.');
    async function request(path,body) {
      const response=await fetch(info.url+path,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+info.token,
        ...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(10000)});
      if(!response.ok)throw new Error('Worker rejected an execution update.');
      return response.json();
    }
    engine={state:()=>request('/state'),acknowledge:(local_id,exchange_id)=>request('/update',{kind:'execution_ack',local_id,exchange_id}),
      rejected:local_id=>request('/update',{kind:'execution_reject',local_id}),canceled:local_id=>request('/update',{kind:'execution_cancel',local_id}),
      fill:(local_id,f)=>request('/update',{kind:'execution_fill',local_id,fill_id:f.id,shares:f.shares,price:f.price,fee:f.fee,status:f.status})};
    engine.stop=()=>request('/update',{kind:'execution_stop'});
    const exchange=createSdkExchange({client,engine,eligible,signGuard});
    await exchange.startup(await engine.state());
    controller=new AutomaticController({engine,exchange});
    signGuard.check=async()=>{
      if(stopping || controller.halted)throw new Error('Automatic session stopped.');
      const state=await engine.state();
      if(state.halted || Date.now()/1000-state.receivedAt>state.maxAge || Date.now()/1000>=state.market.end-2)throw new Error('Execution state expired.');
      const intent=signGuard.intent;
      const current=intent && state.intents.find(i=>i.localId===intent.localId);
      if(intent && (!current || current.cancel || current.price!==intent.price || current.shares!==intent.shares))throw new Error('Order signal changed before signing.');
    };
    console.log('Automatic session started. Maker-only, one market, maximum spend '+capital+', maximum loss '+options.maxLoss+'.');
    console.log('Recording: '+info.run+' | Ctrl+C stops new orders and requests cancellation of session orders.');
    const end=Date.now()+Number(options.seconds)*1000;
    while(!stopping && !controller.halted && Date.now()<end && child.exitCode===null) {
      await controller.step();
      if(!controller.halted)await new Promise(resolve=>setTimeout(resolve,500));
    }
    const reason=controller.halted || (stopping?'user_stop':'session_finished');
    console.log('Automatic session stopped: '+reason);
    if(reason==='ambiguous_submission_requires_reconciliation')console.log('A submission may have been accepted. Inspect open orders in Polymarket before restarting.');
  } finally {
    signGuard.check=null;
    if(engine) {try{await engine.stop();}catch{} }
    if(controller) {
      const failures=await controller.stop(controller.halted||'session_finished');
      if(failures.length)console.log('Cancellation not confirmed for: '+failures.join(', ')+'. Check Polymarket open orders.');
      // A match can race a cancellation. Retain the worker while reconciling
      // confirmed fills, including fills of orders already canceled locally.
      const deadline=Date.now()+15000;
      let unresolved=false;
      do {
        unresolved=false;
        for(const [localId,orderId] of controller.submitted) {
          try {
            const updates=await controller.exchange.fills(orderId);
            for(const fill of updates) {
              if(fill.orderId!==orderId || fill.status==='FAILED')throw new Error('Invalid final execution.');
              if(fill.status!=='CONFIRMED'){unresolved=true;continue;}
              await engine.fill(localId,fill);
            }
          } catch {unresolved=true;}
        }
        if(unresolved && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,500));
      } while(unresolved && Date.now()<deadline);
      if(unresolved)console.log('Final account fills are not fully reconciled. Check Polymarket trades before restarting; this journal may be incomplete.');
    }
    process.removeListener('SIGINT',stoppingSignal);process.removeListener('SIGTERM',stoppingSignal);
    child.kill();
  }
}
