/* Retain empty table columns from PDF/OCR coordinates instead of shifting data. */
(function(root){
'use strict';
function positionedText(words){
 const sorted=words.filter(w=>w.text.trim()).sort((a,b)=>a.y-b.y||a.x-b.x),lines=[];
 for(const word of sorted){
  let line=lines.find(l=>Math.abs(l.y-word.y)<Math.max(3,word.h*.5));
  if(!line){line={y:word.y,words:[]};lines.push(line);}line.words.push(word);
 }
 let columns=null;
 return lines.sort((a,b)=>a.y-b.y).map(line=>{
  const cells=[];
  for(const w of line.words.sort((a,b)=>a.x-b.x)){
   const previous=cells[cells.length-1];
   const threshold=Math.max(10,previous?previous.charWidth*2.2:10);
   if(previous&&w.x-previous.end<=threshold){previous.text+=' '+w.text;previous.end=Math.max(previous.end,w.x+w.w);}
   else cells.push({text:w.text,x:w.x,end:w.x+w.w,charWidth:w.w/Math.max(w.text.length,1)});
  }
  const labels=cells.map(c=>c.text.toLowerCase().replace(/[\s._]/g,''));
  if(labels.some(s=>['description','descriptionofgoods','itemdescription','product','productname','item','itemname','품명','품목','제품명'].includes(s))&&labels.some(s=>['qty','quantity','수량'].includes(s))){
   columns=cells.map(c=>(c.x+c.end)/2);
   return cells.map(c=>c.text).join('\t');
  }
  if(columns){
   if(/^(grand\s*total|sub\s*total|total|합계|총계)/i.test(cells[0]?.text||''))columns=null;
   else{
    const values=columns.map(()=>'');
    for(const cell of cells){
     const center=(cell.x+cell.end)/2;
     let index=0;
     for(let i=1;i<columns.length;i++)if(Math.abs(center-columns[i])<Math.abs(center-columns[index]))index=i;
     values[index]+=(values[index]?' ':'')+cell.text;
    }
    return values.join('\t');
   }
  }
  return cells.map(c=>c.text).join('\t');
 }).join('\n');
}
if(typeof module!=='undefined'&&module.exports)module.exports={positionedText};
else root.TPImportLayout={positionedText};
})(typeof window==='undefined'?globalThis:window);
