const {test}=require('node:test');const assert=require('node:assert/strict');
const {bind}=require('../app/static/packing-refresh-review.js');
function input(value){return {value,message:'',listeners:{},get valueAsNumber(){return Number(this.value)},get validity(){return {valid:this.value!==''&&Number.isFinite(Number(this.value))&&Number(this.value)>0&&!this.message}},setCustomValidity(message){this.message=message},addEventListener(event,fn){this.listeners[event]=fn}}}
function setup(netValue,grossValue,language='en'){
 const net=input(netValue),gross=input(grossValue),listeners={};
 const row={querySelector:selector=>selector.includes('net_weight')?net:gross};
 const form={querySelectorAll:()=>[row],addEventListener:(event,fn)=>listeners[event]=fn,reportValidity:()=>net.validity.valid&&gross.validity.valid};
 bind(form,language);return {net,gross,form,listeners};
}
test('gross below net is invalid without altering either value; correcting gross clears the error',()=>{
 const {net,gross}=setup('3','2');assert.match(gross.message,/at least net/);assert.equal(net.value,'3');assert.equal(gross.value,'2');
 gross.value='3';gross.listeners.input();assert.equal(gross.message,'');assert.equal(gross.validity.valid,true);
 net.value='4';net.listeners.change();assert.match(gross.message,/at least net/);
});
test('Korean correction is attached to gross field and submit blocks invalid state',()=>{
 const {gross,listeners}=setup('3','2','ko');let prevented=false;
 listeners.submit({preventDefault(){prevented=true}});assert.equal(prevented,true);assert.match(gross.message,/총중량은 순중량 이상/);
});
test('blank or individually invalid weights keep native validation; equal micro weights are valid',()=>{
 for(const values of [['','2'],['2',''],['NaN','2']]){const {gross}=setup(...values);assert.equal(gross.message,'')}
 const {gross,listeners}=setup('0.000001','0.000001');assert.equal(gross.message,'');
 listeners.submit({preventDefault(){assert.fail('valid values must submit')}});
});
