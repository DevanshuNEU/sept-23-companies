const fs=require("fs");
const M=JSON.parse(fs.readFileSync("refresh/work/final_merge.json","utf8"));
let src=fs.readFileSync("dashboard/data.js","utf8");
const start=src.indexOf("window.COMPANIES = [")+"window.COMPANIES = [".length, end=src.indexOf("window.RULED_OUT");
const body=src.slice(start,end);
let spans=[],depth=0,s=-1,inStr=false,q="",esc=false;
for(let i=0;i<body.length;i++){const ch=body[i];
  if(inStr){if(esc)esc=false;else if(ch==="\\")esc=true;else if(ch===q)inStr=false;continue;}
  if(ch==="\""||ch==="'"){inStr=true;q=ch;continue;}
  if(ch==="{"){if(depth===0)s=i;depth++;} else if(ch==="}"){depth--;if(depth===0&&s>=0){spans.push([s,i+1]);s=-1;}}}
// Top-level fields of one entry literal: [{key, a, b}] where txt.slice(a,b) is `key:value`. Strings and nested
// objects are skipped properly, so text like "legal name: X" inside a note can never be mistaken for a field.
const topFields=txt=>{const out=[];let d=0,iS=false,qq="",e2=false;
  for(let k=0;k<txt.length;k++){const ch=txt[k];
    if(iS){if(e2)e2=false;else if(ch==="\\")e2=true;else if(ch===qq)iS=false;continue;}
    if(ch==="\""||ch==="'"){iS=true;qq=ch;continue;}
    if(ch==="{"||ch==="["){d++;if(d===1)continue;}
    else if(ch==="}"||ch==="]"){d--;if(d===0&&out.length)out[out.length-1].b=out[out.length-1].b||k;continue;}
    if(d!==1)continue;
    if(ch===","){if(out.length&&!out[out.length-1].b)out[out.length-1].b=k;continue;}
    const m=/^([A-Za-z_$][\w$]*)\s*:/.exec(txt.slice(k,k+40));
    if(m&&(!out.length||out[out.length-1].b)){out.push({key:m[1],a:k,b:0});k+=m[0].length-1;}}
  return out;};
const dropKeys=(txt,keys)=>{const f=topFields(txt).filter(x=>keys.includes(x.key)).sort((x,y)=>y.a-x.a);
  for(const x of f){let b=x.b;const rest=txt.slice(b);const m=/^\s*,\s*/.exec(rest);if(m)b+=m[0].length;txt=txt.slice(0,x.a)+txt.slice(b);}
  return txt;};
let out="",cur=0,n=0;
for(const [a,b] of spans){
  let txt=body.slice(a,b);
  const id=(txt.match(/id:"([^"]+)"/)||[])[1], m=id&&M[id];
  if(m){
    txt=dropKeys(txt,["level","noRoleInBand","noRoleWhy"]);
    const lv={}; ["levelChecked","reqsRead","inBand","levelOk","entryReq","entryTier","entryTierName","entryBasis","entryPosted",
      "entryUrl","entryLoc","lowestBar","start2027","comp","unverified","eligQuote"].forEach(k=>{ if(m[k]!==undefined&&m[k]!==null) lv[k]=m[k]; });
    if(m.noRoleInBand){ lv.levelChecked=true; lv.levelOk=false; lv.reqsRead=m.reqsRead; }
    let ins="level:"+JSON.stringify(lv)+", ";
    if(m.noRoleInBand) ins+="noRoleInBand:true, noRoleWhy:"+JSON.stringify(m.noRoleWhy)+", ";
    if(m.reqs){ txt=dropKeys(txt,["reqs"]); ins+="reqs:"+JSON.stringify(m.reqs)+", ";
      const v=topFields(txt).find(x=>x.key==="velocity"); if(v) txt=txt.slice(0,v.a)+"velocity:"+m.reqs.length+txt.slice(v.b); }
    const nm=topFields(txt).find(x=>x.key==="name"); txt=txt.slice(0,nm.a)+ins+txt.slice(nm.a); n++;
  }
  out+=body.slice(cur,a)+txt; cur=b;
}
out+=body.slice(cur);
fs.writeFileSync("dashboard/data.js", src.slice(0,start)+out+src.slice(end));
console.log("rewrote",n);
