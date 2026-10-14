"""Private course reports and cancellation previews. No invented refund confirmation."""
import re, time
import ride_refunds
from urllib.parse import parse_qs, urlparse
from journey_experience import owned

def cancellation(ride):
    status = ride.get('status'); payment = ride.get('payment_status') or 'unpaid'
    automatic = status in ('searching', 'offered', 'payment_failed', 'payment_canceled') and payment in ('unpaid', 'failed')
    if automatic:
        message = 'Cette course sera annulée. Aucun frais d’annulation ne sera ajouté.'
    elif status in ('completed', 'cancelled'):
        message = 'Cette course est terminée. Vous pouvez demander de l’aide concernant son paiement.'
    elif payment not in ('unpaid', 'failed') or status == 'awaiting_payment':
        message = 'Une vérification du paiement est nécessaire. Envoyez une demande à l’assistance pour l’annulation ou le remboursement. La course reste active tant que la demande n’est pas traitée.'
    else:
        message = 'Le chauffeur a déjà accepté. Demandez l’annulation à l’assistance ; la course reste active tant que la demande n’est pas traitée.'
    return {'automatic': automatic, 'fee': 0 if automatic else None, 'currency': 'XOF', 'message': message, 'support_category': 'payment' if payment not in ('unpaid', 'failed') else 'driver'}

def report(ride):
    fare = int(ride.get('fare') or 0); payment_status = ride.get('payment_status') or 'unpaid'
    confirmed = fare if payment_status in ('paid', 'fully_paid') else int(ride.get('deposit_amount') or 0) if payment_status == 'deposit_paid' else 0
    return {k: ride.get(k) for k in ('id', 'pickup', 'destination', 'vehicle', 'driver_name', 'fare', 'payment', 'payment_status', 'status', 'created_at', 'departure_date', 'departure_time')} | {
        'fare': fare, 'currency': 'XOF', 'confirmed_amount': confirmed, 'balance_due': max(0, fare-confirmed),
        'payment_evidence': 'driver_confirmation' if ride.get('payment') == 'Espèces' and confirmed else 'provider_confirmation' if confirmed else 'not_confirmed',
        'document_type': 'course_report', 'generated_at': int(time.time()),
        'notice': 'Rapport de course. Il ne remplace pas une facture ni le reçu de votre prestataire de paiement.'}

def handle_get(app, path, db):
    match = re.fullmatch(r'/api/rides/([^/]+)/(report|cancellation-preview)', path)
    if not match:
        return False
    token = (parse_qs(urlparse(app.path).query).get('token') or [''])[0]
    if not app.check_rate('journey-service', 60, 60):
        return True
    with db() as conn:
        ride = owned(conn, match[1], token)
    if not ride:
        app.sendj({'error': 'Non autorisé'}, 401); return True
    with db() as conn:
        result={**report(ride),**ride_refunds.fields(conn,ride['id'])} if match[2]=='report' else ride_refunds.preview(conn,ride)
    if result.get('refund',{}):
        result['refunded_amount']=result['refund']['amount'] if result['refund']['status']=='confirmed' else 0
    if match[2]=='report' and ride.get('status')=='cancelled':result['balance_due']=0
    app.sendj(result); return True
