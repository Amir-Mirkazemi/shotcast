(function(){
var box=document.getElementById("call"), data=JSON.parse(box.dataset.seasons), names=JSON.parse(box.dataset.names);
var KEY="shotcast-picks-v1";
function load(){try{return JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){return {}}}
function save(p){try{localStorage.setItem(KEY,JSON.stringify(p))}catch(e){}}
function esc(t){return String(t).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
var picks=load(), lg=Object.keys(data)[0];
var sel=document.getElementById("call-lg");
Object.keys(data).forEach(function(k){var o=document.createElement("option");o.value=k;o.textContent=names[k]||k;sel.appendChild(o)});
sel.onchange=function(){lg=sel.value;draw()};
function draw(){
  var d=data[lg]; if(!d){box.innerHTML='<p class="note">No projections yet.</p>';return}
  var mine=picks[lg]||[];
  var rows=d.players.map(function(p,i){
    var on=mine.indexOf(p.athlete_id)>=0;
    return '<tr><td class="rk">'+(i+1)+'</td><td class="who">'+esc(p.player)+'<small>'+esc(p.team)+'</small></td>'+
      '<td class="n">'+p.so_far+'</td><td class="n hl"><b>'+Math.round(p.proj)+'</b></td>'+
      '<td class="n"><button class="pick" data-id="'+p.athlete_id+'" aria-pressed="'+on+'">'+(on?"Picked":"Pick")+'</button></td></tr>'}).join("");
  var chosen=d.players.filter(function(p){return mine.indexOf(p.athlete_id)>=0});
  var yours=chosen.length?('<h2>Your picks</h2><ul class="plain">'+chosen.map(function(p){
    return '<li><b>'+esc(p.player)+'</b> ('+esc(p.team)+'): '+p.so_far+' on target so far, the model projects '+Math.round(p.proj)+
      ' (range '+p.lo+'-'+p.hi+')</li>'}).join("")+'</ul><p class="note">The model’s top pick is <b>'+esc(d.players[0].player)+
      '</b> ('+Math.round(d.players[0].proj)+'). Your picks stay in this browser only.</p>')
    :'<p class="note">Pick up to three players you think will finish with the most shots on target.</p>';
  box.innerHTML=yours+'<h2>'+esc(names[lg]||lg)+'<small>season '+esc(d.season)+', model projection</small></h2>'+
    '<div class="tablewrap"><table><thead><tr><th class="n">#</th><th>Player</th><th class="n">So far</th>'+
    '<th class="n">Projected</th><th class="n"></th></tr></thead><tbody>'+rows+'</tbody></table></div>';
  box.querySelectorAll(".pick").forEach(function(b){b.onclick=function(){
    var id=b.dataset.id, cur=(picks[lg]||[]).slice(), i=cur.indexOf(id);
    if(i>=0)cur.splice(i,1);else if(cur.length<3)cur.push(id);else return;
    picks[lg]=cur;save(picks);draw()}});
}
draw();
})();
