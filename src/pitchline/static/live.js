(function(){
var box=document.getElementById("live"), meta=document.getElementById("live-meta"), url=box.dataset.src;
var names=JSON.parse(box.dataset.names), fc=JSON.parse(box.dataset.fc||"{}");
function esc(t){return String(t).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function row(m){
  var live=m.state==="in", done=m.state==="post", t=new Date(m.kickoff);
  var when=live?(m.clock||m.detail):(done?"Full time":t.toLocaleTimeString([], {hour:"numeric",minute:"2-digit"}));
  var sc=(live||done)?(m.home_score+" - "+m.away_score):"vs";
  var f=fc[m.league+":"+m.id], line="";
  if(f&&f.length){line='<span class="fcl">Forecast, shots on target: '+f.map(function(p){
    return esc(p.p)+' ('+esc(p.t)+') '+p.x.toFixed(2)}).join(' · ')+'</span>'}
  return '<li class="lv'+(live?' on':'')+'"><span class="d">'+esc(when)+'</span><span class="h">'+esc(m.home)+
    '</span><b class="sc">'+esc(sc)+'</b><span class="a">'+esc(m.away)+'</span><span class="lg"></span>'+line+'</li>';
}
function draw(d){
  var by={};d.matches.forEach(function(m){(by[m.league]=by[m.league]||[]).push(m)});
  var html="";
  Object.keys(by).sort(function(a,b){
    var la=by[a].some(function(m){return m.state==="in"}), lb=by[b].some(function(m){return m.state==="in"});
    return (lb-la)||String(names[a]||a).localeCompare(names[b]||b)}).forEach(function(k){
    html+='<h2>'+esc(names[k]||k)+'</h2><ul class="results">'+by[k].map(row).join("")+'</ul>'});
  box.innerHTML=html||'<p class="note">No matches in the window around today.</p>';
  var age=Math.round((Date.now()-new Date(d.updated))/60000);
  meta.textContent="Updated "+(age<1?"just now":age+" min ago")+(d.failed.length?" · unavailable: "+d.failed.join(", "):"");
}
function load(){fetch(url+"?t="+Date.now()).then(function(r){if(!r.ok)throw 0;return r.json()}).then(draw)
  .catch(function(){meta.textContent="Live scores are not available right now."})}
load();setInterval(load,60000);
})();
