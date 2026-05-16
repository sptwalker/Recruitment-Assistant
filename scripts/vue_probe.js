(function(){
  var nodes = document.querySelectorAll('[class*="attachment-resume-btns"] use');
  for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i];
    var href = el.getAttribute('href') || el.getAttribute('xlink:href') || '';
    if (!/download/i.test(href)) continue;
    var div = el.closest('[class*="icon-content"]');
    var vm = div ? div.__vue__ : null;
    if (!vm) continue;
    var pvm = vm.$parent;
    if (!pvm) continue;
    var downloadUrl = pvm.href || '';
    if (!downloadUrl) continue;

    // Trigger download via temporary <a> element
    var a = document.createElement('a');
    a.href = downloadUrl;
    a.download = '';
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

    return JSON.stringify({success: true, url: downloadUrl.slice(0, 120)});
  }
  return JSON.stringify({error: 'not found'});
})()
