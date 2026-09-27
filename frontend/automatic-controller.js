// Transport-independent lifecycle controller for a user-launched executor.
// No SDK connection, key, process startup or live transaction runs on import.
export class AutomaticController {
  constructor({engine, exchange, now = () => Date.now()/1000}) {
    this.engine=engine; this.exchange=exchange; this.now=now;
    this.halted=null; this.busy=false; this.submitted=new Map();
  }
  async cancelKnown() {
    const failures=[];
    for(const [localId,exchangeId] of this.submitted) {
      try {
        const result=await this.exchange.cancel(exchangeId);
        if(!result.canceled?.includes(exchangeId)) throw new Error('Cancellation not acknowledged.');
        await this.engine.canceled(localId);
      } catch {failures.push(exchangeId);}
    }
    return failures;
  }
  async stop(reason='user_stop') {
    this.halted=reason;
    // Cancel only IDs created by this session; never unrelated account orders.
    return this.cancelKnown();
  }
  async step() {
    if(this.busy || this.halted) return;
    this.busy=true;
    try {
      let pendingSettlement=false;
      // Reconcile every accepted order, including one already canceled locally.
      // Until CONFIRMED, reservations remain held and no imaginary hedge exists.
      for(const [localId,exchangeId] of this.submitted) {
        const updates=await this.exchange.fills(exchangeId);
        for(const fill of updates) {
          if(fill.orderId!==exchangeId) throw new Error('Execution identity mismatch.');
          if(fill.status==='FAILED') throw new Error('Account fill failed settlement.');
          if(fill.status!=='CONFIRMED') {pendingSettlement=true;continue;}
          await this.engine.fill(localId,fill);
        }
      }
      const state=await this.engine.state();
      if(state.halted) {await this.stop(state.halted);return;}
      if(!Number.isFinite(state.receivedAt) || !Number.isFinite(state.maxAge) || state.maxAge<=0 || this.now()-state.receivedAt>state.maxAge || this.now()<state.receivedAt) {
        await this.stop('stale_execution_state');return;
      }
      for(const intent of state.intents) {
        if(this.halted) break;
        const accepted=this.submitted.get(intent.localId);
        if(intent.cancel) {
          if(!accepted) {await this.engine.rejected(intent.localId);continue;}
          const result=await this.exchange.cancel(accepted);
          if(!result.canceled?.includes(accepted)) throw new Error('Cancellation not acknowledged.');
          await this.engine.canceled(intent.localId);
          continue;
        }
        if(accepted) continue;
        if(pendingSettlement) continue;
        await this.exchange.preflight(intent,state);
        // Signals can change while funding, eligibility or metadata is fetched.
        const latest=await this.engine.state();
        const current=latest.intents.find(i=>i.localId===intent.localId);
        if(latest.halted || !current || current.cancel || this.halted) continue;
        if(this.now()-latest.receivedAt>latest.maxAge || this.now()<latest.receivedAt) {
          await this.stop('stale_execution_state');break;
        }
        if(current.price!==intent.price || current.shares!==intent.shares || current.side!==intent.side) throw new Error('Intent mutated during preflight.');
        // Transport failure is ambiguous: NEVER retry a possibly accepted order.
        let response;
        try {response=await this.exchange.place(current,latest);}
        catch {this.halted='ambiguous_submission_requires_reconciliation';await this.cancelKnown();return;}
        if(!response.ok) {await this.engine.rejected(current.localId);continue;}
        if(!response.orderId || [...this.submitted.values()].includes(response.orderId)) throw new Error('Invalid exchange acknowledgement.');
        this.submitted.set(current.localId,response.orderId);
        await this.engine.acknowledge(current.localId,response.orderId);
        if(this.halted) {await this.cancelKnown();return;}
      }
    } catch {
      await this.stop('execution_reconciliation_failed');
    } finally {this.busy=false;}
  }
}
