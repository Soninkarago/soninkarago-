"""Bounded free-plan failover. No upgrade, tool, or paid-provider fallback."""
import json,os,re,time,threading,unicodedata
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError
GROQ_MODELS=('openai/gpt-oss-120b','openai/gpt-oss-20b')
GEMINI_MODEL='gemini-3.5-flash-lite'
_lock=threading.Lock();_cooldown={}

def groq_ready():return bool(os.getenv('GROQ_API_KEY')) and os.getenv('ASSISTANT_FREE_ACCOUNT_CONFIRMED')=='yes' and os.getenv('ASSISTANT_ZDR_CONFIRMED')=='yes'
def google_ready():return bool(os.getenv('GEMINI_API_KEY')) and os.getenv('ASSISTANT_GOOGLE_FREE_CONFIRMED')=='yes' and os.getenv('ASSISTANT_GOOGLE_GENERIC_ONLY_CONFIRMED')=='yes'

def generic_topics(question):
 # Only fixed identifiers leave this function. No substring of the question is sent.
 text=unicodedata.normalize('NFKD',question.lower());text=''.join(c for c in text if not unicodedata.combining(c))
 tokens=set(re.findall(r'[a-z]+',text))
 vocabulary={'services':{'moto','minicar','taxi','village','regions','services'},'identity':{'plaque','photo','chauffeur','vehicule','marque'},'code':{'code','pin','demarrer'},'fare':{'prix','paiement','paye','payer','devis','remboursement'},'stops':{'arret','arrets','detour'},'tracking':{'suivi','gps','position','reseau'},'support':{'assistance','aide','reclamation'},'history':{'historique','avis','note','facture'},'privacy':{'donnees','confidentialite','supprimer','compte'},'legal':{'loi','lois','droit','juridique','permis'},'emergency':{'urgence','danger','accident','blesse','police','samu'}}
 return sorted(k for k,words in vocabulary.items() if tokens & words)

class NoRedirect(HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None

def google_select(question,topics,timeout):
 keywords=generic_topics(question)
 if not keywords:return 'unknown'
 payload={'contents':[{'parts':[{'text':'Choisis uniquement un identifiant parmi '+json.dumps(list(topics))+'. Si incertain: unknown. Mots-clés génériques : '+json.dumps(keywords)}]}], 'generationConfig':{'maxOutputTokens':512,'temperature':0}}
 request=Request('https://generativelanguage.googleapis.com/v1beta/models/'+GEMINI_MODEL+':generateContent',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','x-goog-api-key':os.environ['GEMINI_API_KEY']},method='POST')
 with build_opener(NoRedirect()).open(request,timeout=timeout) as response:
  raw=response.read(65537)
  if len(raw)>65536:raise ValueError('Oversized response')
 candidate=json.loads(raw)['candidates'][0]
 if candidate.get('finishReason')!='STOP':raise ValueError('Incomplete response')
 answer=''.join(p.get('text','') for p in candidate['content']['parts']).strip()
 if answer not in topics:raise ValueError('Unapproved answer')
 return answer

def select(question,groq_call,topics,allow_google=False):
 candidates=[]
 if groq_ready():candidates.extend(('groq:'+model,lambda t,m=model:groq_call(question,model=m,timeout=t)) for model in GROQ_MODELS)
 if allow_google and google_ready():candidates.append(('google:'+GEMINI_MODEL,lambda t:google_select(question,topics,t)))
 deadline=time.monotonic()+24
 for name,call in candidates:
  with _lock:
   if _cooldown.get(name,0)>time.monotonic():continue
  remaining=deadline-time.monotonic()
  if remaining<0.1:break
  try:
   topic=call(min(7,remaining))
   if topic not in topics:raise ValueError('Unapproved answer')
   return topic,name
  except Exception as error:
   # Pause failing engines; no persistent question/error contents or keys are logged.
   wait=60 if isinstance(error,HTTPError) and error.code==429 else 30
   with _lock:_cooldown[name]=time.monotonic()+wait
 raise RuntimeError('All enabled free engines unavailable')
