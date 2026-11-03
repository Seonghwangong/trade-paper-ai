const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/workspace_storage.py'),'utf8').match(/STORAGE_SCRIPT = r"""([\s\S]*?)"""/)[1];
function backing(){const rows=new Map();return {rows,getItem:k=>rows.get(k)??null,setItem:(k,v)=>rows.set(k,String(v)),removeItem:k=>rows.delete(k)};}
function editor(window,prefix){const ctx=vm.createContext({window});vm.runInContext(source.replace('__STORAGE_PREFIX__',JSON.stringify(prefix)),ctx);return vm.runInContext('({localStorage,sessionStorage})',ctx);}

test('two accounts on one browser cannot read or clear each other’s saved workspace data',()=>{
  const window={localStorage:backing(),sessionStorage:backing()};
  const a=editor(window,'account-a:'),b=editor(window,'account-b:');
  for(const kind of ['localStorage','sessionStorage']){
    for(const key of ['trade-paper-ai-invoice-draft','trade-paper-ai-smart-templates','trade-paper-ai-item-library','trade-paper-ai-buyer-context','trade-paper-ai-favorite-values','trade-paper-ai-recent-values','trade-paper-ai-last-work']){
      a[kind].setItem(key,'private-a');assert.equal(b[kind].getItem(key),null);
      b[kind].setItem(key,'private-b');assert.equal(a[kind].getItem(key),'private-a');
      a[kind].removeItem(key);assert.equal(b[kind].getItem(key),'private-b');
    }
  }
});
test('same account returns to its draft; unidentified legacy data remains untouched and unclaimed',()=>{
  const window={localStorage:backing(),sessionStorage:backing()};
  const key='trade-paper-ai-invoice-draft';window.localStorage.setItem(key,'legacy');
  const a=editor(window,'account-a:');assert.equal(a.localStorage.getItem(key),null);
  a.localStorage.setItem(key,'new draft');assert.equal(editor(window,'account-a:').localStorage.getItem(key),'new draft');
  a.localStorage.removeItem(key);assert.equal(window.localStorage.getItem(key),'legacy');
});
test('signed-out pages neither read nor mutate browser workspace storage',()=>{
  const window={localStorage:backing(),sessionStorage:backing()};
  const anon=editor(window,'');
  for(const kind of ['localStorage','sessionStorage']){
    window[kind].setItem('trade-paper-ai-invoice-draft','legacy');
    assert.equal(anon[kind].getItem('trade-paper-ai-invoice-draft'),null);
    anon[kind].setItem('trade-paper-ai-invoice-draft','overwrite');anon[kind].removeItem('trade-paper-ai-invoice-draft');
    assert.equal(window[kind].getItem('trade-paper-ai-invoice-draft'),'legacy');
  }
});
test('unavailable storage does not throw until the existing guarded storage operation',()=>{
  const window={};Object.defineProperty(window,'localStorage',{get(){throw new Error('storage blocked');}});
  const a=editor(window,'account-a:');assert.throws(()=>a.localStorage.getItem('draft'),/storage blocked/);
  assert.equal(editor(window,'').localStorage.getItem('draft'),null);
});
