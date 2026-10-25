const {test}=require('node:test');
const assert=require('node:assert/strict');
const {positionedText}=require('../app/static/import_layout.js');
const word=(text,x,y,w)=>({text,x,y,w,h:10});
test('preserves a blank HS code column in a right-aligned invoice PDF',()=>{
 const result=positionedText([
  word('Item',60,100,20),word('HS Code',245,100,45),word('Qty',330,100,20),word('Unit Price',395,100,50),word('Total',510,100,30),
  word('Cotton shirts',60,130,80),word('100',332,130,18),word('USD 8.50',400,130,45),word('USD 850.00',480,130,60),
  word('TOTAL: USD 850',400,160,140)
 ]);
 assert.equal(result.split('\n')[1],'Cotton shirts\t\t100\tUSD 8.50\tUSD 850.00');
 assert.equal(result.split('\n')[2],'TOTAL: USD 850');
});
test('joins OCR words within one cell but keeps wide column boundaries',()=>{
 const result=positionedText([word('Description',50,50,100),word('Qty',400,50,30),word('Unit',500,50,40),word('Price',547,50,45),word('Steel',50,100,40),word('bolts',96,100,40),word('50',400,100,20),word('2.00',500,100,40)]);
 assert.equal(result,'Description\tQty\tUnit Price\nSteel bolts\t50\t2.00');
});
