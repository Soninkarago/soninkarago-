const CACHE_NAME='soninkarago-v13-security-push';
const APP_SHELL=[
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/icon-192.png',
  '/icon-512.png',
  '/confidentialite',
  '/conditions',
  '/mentions-legales',
  '/suppression-compte'
];

self.addEventListener('install',event=>{
  event.waitUntil(caches.open(CACHE_NAME).then(cache=>cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate',event=>{
  event.waitUntil(
    caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE_NAME).map(key=>caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener('fetch',event=>{
  const request=event.request;
  const url=new URL(request.url);
  if(request.method!=='GET' || url.pathname.startsWith('/api/')) return;

  if(request.mode==='navigate'){
    event.respondWith(
      fetch(request).then(response=>{
        const copy=response.clone();
        caches.open(CACHE_NAME).then(cache=>cache.put('/',copy));
        return response;
      }).catch(()=>caches.match('/'))
    );
    return;
  }

  event.respondWith(
    caches.match(request).then(cached=>cached || fetch(request).then(response=>{
      if(response.ok && url.origin===self.location.origin){
        const copy=response.clone();
        caches.open(CACHE_NAME).then(cache=>cache.put(request,copy));
      }
      return response;
    }))
  );
});

self.addEventListener('push',event=>{
  let data={title:'SoninkaraGo',body:'Vous avez une nouvelle information.'};
  try{data={...data,...event.data.json()};}catch(e){if(event.data)data.body=event.data.text();}
  event.waitUntil(self.registration.showNotification(data.title||'SoninkaraGo',{
    body:data.body||'',
    icon:'/icon-192.png',
    badge:'/icon-192.png',
    tag:data.tag||'soninkarago-push',
    data:{url:data.url||'/'}
  }));
});

self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const target=(event.notification.data&&event.notification.data.url)||'/';
  event.waitUntil((async()=>{
    const list=await clients.matchAll({type:'window',includeUncontrolled:true});
    for(const client of list){
      if('focus' in client){await client.focus();if('navigate' in client)await client.navigate(target);return;}
    }
    if(clients.openWindow) return clients.openWindow(target);
  })());
});
