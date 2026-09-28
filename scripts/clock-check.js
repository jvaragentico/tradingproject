import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';

export function clockOffset(startMs,endMs,dateHeader) {
  const serverMs=Date.parse(dateHeader);
  if(!Number.isFinite(serverMs) || endMs<startMs || endMs-startMs>3000)throw new Error('Could not verify the computer clock against Polymarket.');
  const offsetMs=serverMs-(startMs+endMs)/2;
  if(Math.abs(offsetMs)>1500)throw new Error('Computer clock differs from Polymarket by '+(offsetMs/1000).toFixed(1)+' seconds. Sync Windows Date & time, then retry. No orders placed.');
  return offsetMs;
}

export async function checkClock(fetcher=fetch,now=Date.now) {
  const start=now();
  const response=await fetcher('https://gamma-api.polymarket.com/markets?limit=1',{signal:AbortSignal.timeout(10000),cache:'no-store'});
  const end=now();
  if(!response.ok)throw new Error('Could not verify the computer clock against Polymarket.');
  return clockOffset(start,end,response.headers.get('date'));
}

if(process.argv[1] && import.meta.url===pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const offset=await checkClock();
    console.log('Clock check passed: '+(offset/1000).toFixed(1)+' seconds from Polymarket server time.');
  } catch(error) {
    console.error(error.message);
    process.exitCode=1;
  }
}
