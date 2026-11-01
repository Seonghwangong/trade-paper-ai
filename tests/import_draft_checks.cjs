const assert=require('node:assert/strict');
const {create}=require('../app/static/import_draft.js');
function memory(){const map=new Map();return {getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k),map};}
const A='a'.repeat(64),B='b'.repeat(64),now=1800000000000;
const draft={status:'draft',note:'Buyer: ABC',mode:'note',label:'Order note',result:{fields:{buyer:'ABC',seller:'',currency:'USD'},items:[{name:'Cup',quantity:'10',unit_price:'3'}],source_text:'Original note',warnings:['note_review'],unmatched_lines:[]}};
{
 const storage=memory(),first=create(storage,A,()=>now);assert.equal(first.load(),null);assert.equal(first.write(draft).ok,true);
 const reload=create(storage,A,()=>now+1000),loaded=reload.load();assert.deepEqual(loaded.result,draft.result);assert.equal(loaded.note,draft.note);
 assert.equal(create(storage,B,()=>now).load(),null);assert.equal(storage.map.size,1);
 assert.equal(reload.clear(),true);assert.equal(create(storage,A,()=>now).load(),null);
}
{
 const storage=memory(),store=create(storage,A,()=>now);store.load();store.write({...draft,status:'saving'});
 assert.equal(create(storage,A,()=>now+1000).load().status,'saving');
 assert.equal(create(storage,A,()=>now+8*86400000).load(),null);assert.equal(storage.map.size,0);
}
{
 const storage=memory(),first=create(storage,A,()=>now),second=create(storage,A,()=>now+1);first.load();first.write(draft);second.load();
 assert.equal(first.write(draft).ok,true); // Page visibility changes alone must not invalidate the other tab.
 assert.equal(second.write({...draft,note:'newer tab'}).ok,true);
 assert.equal(first.write({...draft,note:'stale tab'}).reason,'conflict');assert.equal(first.clear(),false);
 assert.equal(create(storage,A,()=>now+2).load().note,'newer tab');
}
{
 const storage=memory(),store=create(storage,A,()=>now);store.load();store.write(draft);
 const second=create(storage,A,()=>now+1);second.load();store.clear();
 assert.equal(second.write(draft).reason,'conflict'); // A completed save cannot be resurrected by a stale tab.
}
{
 const storage=memory();storage.setItem('tradepaper-import-draft-v1:'+A,'not-json');assert.equal(create(storage,A,()=>now).load(),null);
 const store=create(storage,A,()=>now);store.load();assert.equal(store.write({...draft,note:'x'.repeat(8001)}).ok,false);assert.equal(store.write({...draft,result:{...draft.result,items:Array(101).fill({name:'Cup'})}}).ok,false);
 assert.equal(create(null,A).write(draft).reason,'unavailable');assert.equal(create(storage,'').write(draft).ok,false);
 const denied=create({getItem(){throw Error('denied');},removeItem(){throw Error('denied');}},A);assert.equal(denied.load(),null);assert.equal(denied.write(draft).reason,'unavailable');
}
console.log('Draft recovery: reload, user isolation, expiry, pending submission, conflicting tabs, success cleanup and storage failures passed.');
