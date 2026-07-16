from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import models
from django.db.models import Sum
from django.http import JsonResponse
from django.urls import reverse
from django.contrib.auth.hashers import make_password, check_password, identify_hasher
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.core.mail import send_mail
from django.utils import timezone
from datetime import datetime, date
from math import ceil
import random
import string
from .models import Signup,Booking,OwnerProfile,OwnerDocument,ParkingLot,PaymentTransaction,Review,Notification,SavedLocation,OTPCode
from .tokens import signup_token_generator


def _notify_owner(parking, notif_type, title, message, link=''):
    """
    Create an in-app notification for the owner of a parking lot.
    Silently no-ops if the lot has no resolvable owner account.
    """
    if not parking or not parking.owner or not parking.owner.owner:
        return

    Notification.objects.create(
        recipient=parking.owner.owner,
        notif_type=notif_type,
        title=title,
        message=message,
        link=link,
    )

def _format_stat(n):
    """Format a raw count into a compact display string, e.g. 1450 -> '1.4K+', 87 -> '87+'."""
    n = n or 0
    if n >= 1000:
        value = n / 1000
        # Drop the decimal when it's a whole number (2.0K -> 2K)
        text = f"{value:.1f}".rstrip('0').rstrip('.')
        return f"{text}K+"
    return f"{n}+"


# Landing Page
def home(request):

    today = date.today()

    featured_qs = ParkingLot.objects.filter(is_active=True).order_by('-created_at')[:3]

    saved_parking_ids = set()
    if request.session.get('user_id'):
        saved_parking_ids = set(
            SavedLocation.objects.filter(
                user_id=request.session['user_id']
            ).values_list('parking_id', flat=True)
        )

    featured_parkings = []

    for parking in featured_qs:

        car_booked = Booking.objects.filter(
            parking_name=parking.parking_name,
            vehicle_type='Car',
            booking_date=today,
            status__in=['Pending', 'Active']
        ).count()

        bike_booked = Booking.objects.filter(
            parking_name=parking.parking_name,
            vehicle_type='Bike',
            booking_date=today,
            status__in=['Pending', 'Active']
        ).count()

        car_available = max(parking.car_capacity - car_booked, 0)
        bike_available = max(parking.bike_capacity - bike_booked, 0)

        featured_parkings.append({
            'parking': parking,
            'car_available': car_available,
            'bike_available': bike_available,
            'average_rating': parking.average_rating(),
            'review_count': parking.review_count(),
            'is_saved': parking.id in saved_parking_ids,
            'is_available': (car_available > 0 or bike_available > 0),
        })

    # ---- Platform-wide stats (hero + stats-strip + floating badges) ----

    active_lots = ParkingLot.objects.filter(is_active=True)
    total_lots = active_lots.count()
    total_users = Signup.objects.filter(role='user').count()

    rating_agg = Review.objects.aggregate(avg=models.Avg('rating'))['avg']
    platform_rating = round(rating_agg, 1) if rating_agg else 5.0

    # Sum of currently-free car+bike slots across every active lot, today.
    total_available_spots = 0
    for lot in active_lots:
        car_booked = Booking.objects.filter(
            parking_name=lot.parking_name, vehicle_type='Car',
            booking_date=today, status__in=['Pending', 'Active']
        ).count()
        bike_booked = Booking.objects.filter(
            parking_name=lot.parking_name, vehicle_type='Bike',
            booking_date=today, status__in=['Pending', 'Active']
        ).count()
        total_available_spots += max(lot.car_capacity - car_booked, 0)
        total_available_spots += max(lot.bike_capacity - bike_booked, 0)

    stats = {
        'total_lots_display': _format_stat(total_lots),
        'total_users_display': _format_stat(total_users),
        'platform_rating': platform_rating,
        'total_available_spots': total_available_spots,
    }

    # ---- Real testimonials, best-rated first ----

    testimonials = (
        Review.objects.select_related('user', 'parking')
        .exclude(comment='')
        .order_by('-rating', '-created_at')[:3]
    )

    context = {
        'featured_parkings': featured_parkings,
        'stats': stats,
        'testimonials': testimonials,
    }

    return render(request, 'index.html', context)



# Authentication Page
def authentication(request):

    if request.method == "POST":

        action = request.POST.get('action')
# SIGNUP
    
        if action == "signup":

            first_name = request.POST.get('first_name')
            last_name = request.POST.get('last_name')
            username = request.POST.get('signup_username')
            email = request.POST.get('email')
            password = request.POST.get('signup_password')
            role = request.POST.get('signup_role', 'user')

            if Signup.objects.filter(username=username).exists():
                messages.error(request,"Username already exists. Please choose another username.")
                return redirect('authentication')

            if Signup.objects.filter(email=email).exists():
                messages.error(request,"Email already registered.")
                return redirect('authentication')

            Signup.objects.create(
                first_name=first_name,
                last_name=last_name,
                username=username,
                email=email,
                password=make_password(password),
                role=role
            )

            messages.success(
                request,
                "Account created successfully. Please login."
            )

            return redirect('authentication')

      
        # LOGIN
    
        elif action == "login":

            username = request.POST.get('login_username')
            password = request.POST.get('login_password')

            user = Signup.objects.filter(username=username).first()

            valid = False

            if user:
                try:
                    # Stored value is a recognised hash - verify normally.
                    identify_hasher(user.password)
                    valid = check_password(password, user.password)
                except ValueError:
                    # Legacy plaintext row (pre-migration). Verify directly,
                    # then upgrade it to a proper hash so it never happens again.
                    if user.password == password:
                        valid = True
                        user.password = make_password(password)
                        user.save(update_fields=['password'])

            if not user or not valid:
                messages.error(
                    request,
                    "Invalid username or password."
                )
                return redirect('authentication')

            if not user.is_active:
                _send_reactivation_otp(user)
                request.session['pending_reactivation_user_id'] = user.id
                messages.info(
                    request,
                    "This account is deactivated. We've emailed you a code to reactivate it."
                )
                return redirect('reactivate_account')

            if user.two_factor_enabled:
                _send_login_otp(user)
                request.session['pending_2fa_user_id'] = user.id
                return redirect('verify_otp')

            # Store session
            request.session['user_id'] = user.id
            request.session['username'] = user.username
            request.session['role'] = user.role

            messages.success(
                request,
                f"Welcome {user.username}!"
            )

            # Redirect by role
            if user.role == 'admin':
                return redirect('admin_dashboard')

            elif user.role == 'owner':
                return redirect('owner_dashboard')

            else:
                return redirect('dashboard')

    return render(request, 'authentication.html')


def _send_login_otp(user):
    """Generate a fresh 6-digit login-2FA code, store it, and email it to the user."""
    code = f"{random.randint(0, 999999):06d}"
    OTPCode.objects.create(user=user, code=code, purpose='login')
    send_mail(
        subject='Your Parkify login code',
        message=f'Hi {user.username}, your login verification code is {code}. It expires in 10 minutes.',
        from_email=None,
        recipient_list=[user.email],
        fail_silently=True,
    )


def _send_reactivation_otp(user):
    """Generate a fresh 6-digit account-reactivation code, store it, and email it to the user."""
    code = f"{random.randint(0, 999999):06d}"
    OTPCode.objects.create(user=user, code=code, purpose='reactivation')
    send_mail(
        subject='Reactivate your Parkify account',
        message=(
            f'Hi {user.username}, your account was deactivated. '
            f'Your reactivation code is {code}. It expires in 10 minutes. '
            f"If you didn't request this, you can ignore this email."
        ),
        from_email=None,
        recipient_list=[user.email],
        fail_silently=True,
    )


# Two-Factor Login Verification
def verify_otp(request):

    pending_id = request.session.get('pending_2fa_user_id')
    if not pending_id:
        return redirect('authentication')

    user = get_object_or_404(Signup, id=pending_id)

    if request.method == "POST":

        if request.POST.get('action') == 'resend':
            _send_login_otp(user)
            messages.success(request, "A new code has been sent to your email.")
            return redirect('verify_otp')

        entered_code = request.POST.get('code', '').strip()

        otp = OTPCode.objects.filter(user=user, is_used=False, purpose='login').order_by('-created_at').first()

        if not otp or otp.code != entered_code or otp.is_expired():
            messages.error(request, "Invalid or expired code. Please try again.")
            return render(request, 'verify_otp.html', {'email': user.email})

        otp.is_used = True
        otp.save(update_fields=['is_used'])

        del request.session['pending_2fa_user_id']

        request.session['user_id'] = user.id
        request.session['username'] = user.username
        request.session['role'] = user.role

        messages.success(request, f"Welcome {user.username}!")

        if user.role == 'admin':
            return redirect('admin_dashboard')
        elif user.role == 'owner':
            return redirect('owner_dashboard')
        else:
            return redirect('dashboard')

    return render(request, 'verify_otp.html', {'email': user.email})


# Two-Factor Authentication toggle
def two_factor_toggle(request):

    if not request.session.get('user_id'):
        return redirect('authentication')

    user = Signup.objects.get(id=request.session['user_id'])
    user.two_factor_enabled = not user.two_factor_enabled
    user.save(update_fields=['two_factor_enabled'])

    if user.two_factor_enabled:
        messages.success(request, "Two-factor authentication enabled. You'll be emailed a code at each login.")
    else:
        messages.success(request, "Two-factor authentication disabled.")

    role = request.session.get('role')
    if role == 'owner':
        return redirect('owner_dashboard')
    elif role == 'admin':
        return redirect('admin_dashboard')
    return redirect('dashboard')


# Delete Account (soft delete: deactivate + log out, data is preserved)
def delete_account(request):

    if not request.session.get('user_id'):
        return redirect('authentication')

    if request.method != "POST":
        return redirect('authentication')

    user = Signup.objects.get(id=request.session['user_id'])
    role = request.session.get('role')

    user.is_active = False
    user.save(update_fields=['is_active'])

    if role == 'owner':
        # Hide the owner's listings from renters; booking/review history is preserved.
        ParkingLot.objects.filter(owner__owner=user).update(is_active=False)

    request.session.flush()
    messages.success(request, "Your account has been deactivated. Contact support if this was a mistake.")
    return redirect('authentication')


# Self-service account reactivation
def reactivate_account(request):

    pending_id = request.session.get('pending_reactivation_user_id')
    if not pending_id:
        return redirect('authentication')

    user = get_object_or_404(Signup, id=pending_id)

    if request.method == "POST":

        if request.POST.get('action') == 'resend':
            _send_reactivation_otp(user)
            messages.success(request, "A new reactivation code has been sent to your email.")
            return redirect('reactivate_account')

        entered_code = request.POST.get('code', '').strip()

        otp = OTPCode.objects.filter(user=user, is_used=False, purpose='reactivation').order_by('-created_at').first()

        if not otp or otp.code != entered_code or otp.is_expired():
            messages.error(request, "Invalid or expired code. Please try again.")
            return render(request, 'reactivate_account.html', {'email': user.email})

        otp.is_used = True
        otp.save(update_fields=['is_used'])

        user.is_active = True
        user.save(update_fields=['is_active'])

        del request.session['pending_reactivation_user_id']

        request.session['user_id'] = user.id
        request.session['username'] = user.username
        request.session['role'] = user.role

        messages.success(request, f"Welcome back, {user.username}! Your account has been reactivated.")
        if user.role == 'owner':
            messages.info(request, "Your parking listings were hidden when you deactivated your account. Re-enable them from 'My Parking Lots' whenever you're ready.")

        if user.role == 'admin':
            return redirect('admin_dashboard')
        elif user.role == 'owner':
            return redirect('owner_dashboard')
        else:
            return redirect('dashboard')

    return render(request, 'reactivate_account.html', {'email': user.email})
# Admin Dashboard
def admin_dashboard(request):

    if not request.session.get('user_id'):
        return redirect('authentication')

    if request.session.get('role') != 'admin':
        return redirect('authentication')

    admin = Signup.objects.get(id=request.session['user_id'])

    pending_owners = OwnerProfile.objects.filter(
        is_verified=False,
        ownerdocument__isnull=False
    ).distinct().prefetch_related('ownerdocument_set')

    owners = OwnerProfile.objects.all()

    parking_lots = ParkingLot.objects.all()

    bookings = Booking.objects.select_related('user').all().order_by('-created_at')

    status_filter = request.GET.get('status', '')
    if status_filter:
        bookings = bookings.filter(status=status_filter)

    total_revenue = PaymentTransaction.objects.filter(
        status='Success'
    ).aggregate(total=models.Sum('amount'))['total'] or 0

    active_bookings_count = Booking.objects.filter(status='Active').count()

    reviews = Review.objects.select_related('parking', 'user').all()

    context = {
        'admin': admin,
        'pending_owners': pending_owners,
        'owners': owners,
        'parking_lots': parking_lots,
        'bookings': bookings,
        'status_filter': status_filter,
        'total_revenue': total_revenue,
        'active_bookings_count': active_bookings_count,
        'reviews': reviews,
    }

    return render(request, 'admin_dashboard.html', context)

#Approve view
def approve_owner(request, owner_id):

    if not request.session.get('user_id') or request.session.get('role') != 'admin':
        return redirect('authentication')

    if request.method != "POST":
        return redirect('admin_dashboard')

    owner = get_object_or_404(OwnerProfile, id=owner_id)

    if not OwnerDocument.objects.filter(owner=owner).exists():
        messages.error(
            request,
            "Cannot approve: this owner has not uploaded any documents yet."
        )
        return redirect('admin_dashboard')

    owner.is_verified = True
    owner.save()

    OwnerDocument.objects.filter(owner=owner).update(status='Verified')

    messages.success(
        request,
        "Owner approved successfully."
    )

    return redirect('admin_dashboard')

#Reject view
def reject_owner(request, owner_id):

    if not request.session.get('user_id') or request.session.get('role') != 'admin':
        return redirect('authentication')

    if request.method != "POST":
        return redirect('admin_dashboard')

    owner = get_object_or_404(OwnerProfile, id=owner_id)

    owner.is_verified = False
    owner.save()

    OwnerDocument.objects.filter(owner=owner).update(status='Rejected')

    messages.error(
        request,
        "Owner rejected."
    )

    return redirect('admin_dashboard')

# User Dashboard
def dashboard(request):

    if not request.session.get('user_id'):
        return redirect('authentication')

    if request.session.get('role') != 'user':
        return redirect('authentication')

    user = Signup.objects.get(id=request.session['user_id'])
    bookings_qs = Booking.objects.filter(user=user).order_by('-created_at')
    bookings = list(bookings_qs[:25])
    total_bookings = bookings_qs.count()
    active_bookings = bookings_qs.filter(status__in=['Pending', 'Active']).count()
    completed_bookings = bookings_qs.filter(status='Completed').count()
    saved_locations = SavedLocation.objects.filter(user=user).count()

    return render(request, 'dashboard.html', {
        'user': user,
        'bookings': bookings,
        'total_bookings': total_bookings,
        'active_bookings': active_bookings,
        'completed_bookings': completed_bookings,
        'recent_bookings': bookings[:6],
        'saved_locations': saved_locations,
    })

# TODO(owner): finish analytics widgets and saved-locations backend wiring; stop short of full CRM.


# User profile - lets a regular user update their name, email and profile picture
def user_profile(request):

    if not request.session.get('user_id'):
        return redirect('authentication')

    if request.session.get('role') != 'user':
        return redirect('authentication')

    user = Signup.objects.get(id=request.session['user_id'])

    if request.method == "POST":

        user.first_name = request.POST.get('first_name', user.first_name)
        user.last_name = request.POST.get('last_name', user.last_name)
        user.email = request.POST.get('email', user.email)

        if request.FILES.get('profile_image'):
            user.profile_image = request.FILES.get('profile_image')

        user.save()

        messages.success(request, "Profile saved successfully.")
        return redirect('dashboard')

    return redirect('dashboard')


# Saved Locations
def saved_locations_toggle(request, parking_id):

    if not request.session.get('user_id'):
        return JsonResponse({'error': 'login_required'}, status=401)

    user = Signup.objects.get(id=request.session['user_id'])
    parking = get_object_or_404(ParkingLot, id=parking_id)
    saved, created = SavedLocation.objects.get_or_create(user=user, parking=parking)

    if not created:
        saved.delete()
        is_saved = False
    else:
        is_saved = True

    return JsonResponse({'saved': is_saved, 'count': SavedLocation.objects.filter(user=user).count()})


def saved_locations_list(request):

    if not request.session.get('user_id'):
        return redirect('authentication')

    user = Signup.objects.get(id=request.session['user_id'])
    saved = SavedLocation.objects.filter(user=user).select_related('parking').order_by('-saved_at')

    return render(request, 'saved_locations.html', {
        'saved_locations': saved,
    })


def saved_location_remove(request, saved_id):

    if not request.session.get('user_id'):
        return redirect('authentication')

    saved = get_object_or_404(SavedLocation, id=saved_id, user_id=request.session['user_id'])

    if request.method == "POST":
        saved.delete()
        messages.success(request, "Saved location removed.")

    return redirect('saved_locations_list')


# Owner Dashboard
def owner_dashboard(request):

    if not request.session.get('user_id'):
        return redirect('authentication')

    if request.session.get('role') != 'owner':
        return redirect('authentication')

    owner = Signup.objects.get(
        id=request.session['user_id']
    )

    parkings = []

    profile_exists = OwnerProfile.objects.filter(
        owner=owner
    ).exists()

    document_exists = False
    verification_status = "Not Started"
    uploaded_doc_types = set()
    total_owner_revenue = 0
    occupancy = 0
    total_owner_bookings = 0
    bookings = []
    revenue_per_lot = []
    start_date = request.GET.get('start_date', '').strip()
    end_date = request.GET.get('end_date', '').strip()

    if profile_exists:
        profile = OwnerProfile.objects.get(owner=owner)

        parkings = ParkingLot.objects.filter(
            owner=profile
        ).order_by('-created_at')

        for parking in parkings:
            parking.avg_rating = parking.average_rating()
            parking.rating_count = parking.review_count()

        document_exists = OwnerDocument.objects.filter(
            owner=profile
        ).exists()

        uploaded_doc_types = set(
            OwnerDocument.objects.filter(owner=profile).values_list('document_type', flat=True)
        )

        if document_exists:
            verification_status = (
                "Approved"
                if profile.is_verified
                else "Pending"
            )

    notifications = Notification.objects.filter(recipient=owner).order_by('-created_at')
    unread_notification_count = notifications.filter(is_read=False).count()

    profile_parkings = ParkingLot.objects.filter(owner__owner=owner)
    owner_parking_names = [p.parking_name for p in profile_parkings]
    total_capacity = sum(
        (p.car_capacity or 0) + (p.bike_capacity or 0) for p in profile_parkings
    )

    owner_bookings_qs = Booking.objects.filter(parking_name__in=owner_parking_names)
    if start_date:
        owner_bookings_qs = owner_bookings_qs.filter(booking_date__gte=start_date)
    if end_date:
        owner_bookings_qs = owner_bookings_qs.filter(booking_date__lte=end_date)

    total_owner_bookings = owner_bookings_qs.count()

    revenue_qs = PaymentTransaction.objects.filter(
        status='Success',
        booking__parking_name__in=owner_parking_names,
    )
    if start_date:
        revenue_qs = revenue_qs.filter(paid_at__date__gte=start_date)
    if end_date:
        revenue_qs = revenue_qs.filter(paid_at__date__lte=end_date)

    total_owner_revenue = revenue_qs.aggregate(total=models.Sum('amount'))['total'] or 0

    recent_bookings_qs = owner_bookings_qs.select_related('user').order_by('-created_at')[:10]
    bookings = list(recent_bookings_qs)

    occupied_total = owner_bookings_qs.filter(
        status__in=['Pending', 'Active'],
        booking_date=date.today(),
    ).count()
    occupancy = 0
    if total_capacity:
        occupancy = min(round((occupied_total / total_capacity) * 100), 100)

    revenue_per_lot = []
    for parking in parkings:
        lot_revenue = PaymentTransaction.objects.filter(
            status='Success',
            booking__parking_name=parking.parking_name,
        )
        if start_date:
            lot_revenue = lot_revenue.filter(paid_at__date__gte=start_date)
        if end_date:
            lot_revenue = lot_revenue.filter(paid_at__date__lte=end_date)

        lot_amount = lot_revenue.aggregate(total=models.Sum('amount'))['total'] or 0
        lot_bookings = Booking.objects.filter(parking_name=parking.parking_name)
        if start_date:
            lot_bookings = lot_bookings.filter(booking_date__gte=start_date)
        if end_date:
            lot_bookings = lot_bookings.filter(booking_date__lte=end_date)

        revenue_per_lot.append({
            'parking': parking,
            'revenue': lot_amount,
            'bookings': lot_bookings.count(),
        })

    context = {
        'owner': owner,
        'parkings': parkings if profile_exists else [],
        'profile_exists': profile_exists,
        'document_exists': document_exists,
        'verification_status': verification_status,
        'uploaded_doc_types': uploaded_doc_types,
        'notifications': notifications,
        'unread_notification_count': unread_notification_count,
        'total_owner_bookings': total_owner_bookings,
        'total_owner_revenue': total_owner_revenue,
        'bookings': bookings,
        'occupancy': occupancy,
        'revenue_per_lot': revenue_per_lot,
        'start_date': start_date,
        'end_date': end_date,
    }

    return render(
        request,
        'owner_dashboard.html',
        context
    )
# TODO(owner): add revenue-per-lot breakdown and booking date-window picker; stop before analytics suite.
# Owner profile
def owner_profile(request):

    if request.session.get('role') != 'owner':
        return redirect('authentication')

    owner_user = Signup.objects.get(
        id=request.session['user_id']
    )

    existing_profile = OwnerProfile.objects.filter(owner=owner_user).first()

    if request.method == "POST":

        # Fields that live on Signup itself (name/email/photo), not OwnerProfile
        owner_user.first_name = request.POST.get('first_name', owner_user.first_name)
        owner_user.last_name = request.POST.get('last_name', owner_user.last_name)
        owner_user.email = request.POST.get('email', owner_user.email)

        if request.FILES.get('profile_image'):
            owner_user.profile_image = request.FILES.get('profile_image')

        owner_user.save()

        def field(name):
            # Not every form that posts here includes every OwnerProfile field
            # (e.g. the dashboard's "My Profile" form has no company fields).
            # Fall back to the value already on file so we never null it out.
            value = request.POST.get(name)
            if value not in (None, ''):
                return value
            return getattr(existing_profile, name, '') if existing_profile else ''

        resolved_full_name = field('full_name')
        resolved_company_name = field('company_name')

        if not resolved_full_name or not resolved_company_name:
            messages.error(
                request,
                "Full Name and Company Name are required. Please fill them in before saving."
            )
            return render(request, 'owner_profile.html', {
                'owner': owner_user,
                'existing_profile': existing_profile,
                'profile_exists': bool(existing_profile),
            })

        OwnerProfile.objects.update_or_create(
            owner=owner_user,

            defaults={
                'full_name': resolved_full_name,
                'company_name': resolved_company_name,
                'registration_no': field('registration_no'),
                'phone': field('phone'),
                'address': field('address'),
            }
        )

        messages.success(request,"Profile saved successfully.")

        return redirect('owner_dashboard')

    return render(request,'owner_profile.html',{
        'owner': owner_user,
        'existing_profile': existing_profile,
        'profile_exists': bool(existing_profile),
    })

# Owner Document
def owner_document(request):

    if request.session.get('role') != 'owner':
        return redirect('authentication')

    owner_user = Signup.objects.get(id=request.session['user_id'])

    try:
        profile = OwnerProfile.objects.get(owner=owner_user)
    except OwnerProfile.DoesNotExist:
        messages.error(request,"Please complete your profile first.")
        return redirect('owner_profile')

    if request.method == "POST":

        document_mapping = {
            'citizenship': 'Citizenship',
            'pan_document': 'PAN Card',
            'business_registration': 'Business Registration',
            'parking_license': 'Parking License',
        }

        uploaded_any = False

        for field_name, document_type in document_mapping.items():

            uploaded_file = request.FILES.get(field_name)

            if uploaded_file:
                OwnerDocument.objects.create(
                    owner=profile,
                    document_id=f"{document_type}-{profile.id}",
                    document_type=document_type,
                    file_url=uploaded_file
                )
                uploaded_any = True

        if uploaded_any:
            messages.success(
                request,
                "Documents uploaded successfully."
            )
        else:
            messages.error(
                request,
                "Please select at least one document to upload."
            )

        return redirect('owner_dashboard')

    return render(request, 'owner_document.html')
def add_parking(request):

    if request.session.get('role') != 'owner':
        return redirect('authentication')

    owner_user = Signup.objects.get(
        id=request.session['user_id']
    )

    try:
        profile = OwnerProfile.objects.get(
            owner=owner_user
        )

    except OwnerProfile.DoesNotExist:

        messages.error(
            request,
            "Complete profile first."
        )

        return redirect('owner_profile')

    if not profile.is_verified:
        messages.error(
            request,
            "Admin verification required."
        )

        return redirect('owner_dashboard')

    context = {}

    if request.method == "POST":
        name = request.POST.get('parking_name', '').strip()
        location = request.POST.get('location', '').strip()
        car_capacity = request.POST.get('car_capacity', '').strip()
        bike_capacity = request.POST.get('bike_capacity', '').strip()
        rate_per_hour = request.POST.get('rate_per_hour', '').strip()

        if not name or not location or not car_capacity or not bike_capacity or not rate_per_hour:
            messages.error(request, "All main fields are required.")
            context.update({
                'parking_name': name,
                'location': location,
                'car_capacity': car_capacity,
                'bike_capacity': bike_capacity,
                'rate_per_hour': rate_per_hour,
                'map_link': request.POST.get('map_link', ''),
                'description': request.POST.get('description', ''),
            })
            return render(request, 'add_parking.html', context)

        try:
            car_capacity = int(car_capacity)
            bike_capacity = int(bike_capacity)
            rate_per_hour = float(rate_per_hour)
            if car_capacity < 0 or bike_capacity < 0 or rate_per_hour <= 0:
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, "Capacity must be whole numbers and rate must be a positive value.")
            context.update({
                'parking_name': name,
                'location': location,
                'car_capacity': car_capacity,
                'bike_capacity': bike_capacity,
                'rate_per_hour': rate_per_hour,
                'map_link': request.POST.get('map_link', ''),
                'description': request.POST.get('description', ''),
            })
            return render(request, 'add_parking.html', context)

        ParkingLot.objects.create(
            owner=profile,
            parking_name=name,
            parking_image=request.FILES.get('parking_image'),
            location=location,
            latitude=request.POST.get('latitude'),
            longitude=request.POST.get('longitude'),
            car_capacity=car_capacity,
            bike_capacity=bike_capacity,
            rate_per_hour=rate_per_hour,
            map_link=request.POST.get('map_link'),
            description=request.POST.get('description')
        )

        messages.success(
            request,
            "Parking lot added successfully."
        )

        return redirect(
            'my_parking_lots'
        )

    return render(
        request,
        'add_parking.html',
        context
    )

#my parking lot
def my_parking_lots(request):

    if request.session.get('role') != 'owner':
        return redirect('authentication')

    owner_user = Signup.objects.get(id=request.session['user_id'])

    profile = OwnerProfile.objects.get(owner=owner_user)
    parkings = ParkingLot.objects.filter(owner=profile).order_by('-created_at')

    for parking in parkings:
        parking.avg_rating = parking.average_rating()
        parking.rating_count = parking.review_count()

    return render(request,'my_parking_lots.html',{'parkings': parkings})

#edit
def edit_parking(request, parking_id):

    if request.session.get('role') != 'owner':
        return redirect('authentication')

    owner_user = Signup.objects.get(id=request.session['user_id'])
    profile = OwnerProfile.objects.get(owner=owner_user)

    parking = get_object_or_404(ParkingLot, id=parking_id, owner=profile)

    if request.method == "POST":
        name = request.POST.get('parking_name', '').strip()
        location = request.POST.get('location', '').strip()
        car_capacity = request.POST.get('car_capacity', '').strip()
        bike_capacity = request.POST.get('bike_capacity', '').strip()
        rate_per_hour = request.POST.get('rate_per_hour', '').strip()

        if not name or not location or not car_capacity or not bike_capacity or not rate_per_hour:
            messages.error(request, "All main fields are required.")
            return render(request, 'edit_parking.html', {
                'parking': parking,
                'parking_name': name,
                'location': location,
                'car_capacity': car_capacity,
                'bike_capacity': bike_capacity,
                'rate_per_hour': rate_per_hour,
                'map_link': request.POST.get('map_link', ''),
                'description': request.POST.get('description', ''),
            })

        try:
            car_capacity = int(car_capacity)
            bike_capacity = int(bike_capacity)
            rate_per_hour = float(rate_per_hour)
            if car_capacity < 0 or bike_capacity < 0 or rate_per_hour <= 0:
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, "Capacity must be whole numbers and rate must be a positive value.")
            return render(request, 'edit_parking.html', {
                'parking': parking,
                'parking_name': name,
                'location': location,
                'car_capacity': request.POST.get('car_capacity', ''),
                'bike_capacity': request.POST.get('bike_capacity', ''),
                'rate_per_hour': request.POST.get('rate_per_hour', ''),
                'map_link': request.POST.get('map_link', ''),
                'description': request.POST.get('description', ''),
            })

        parking.parking_name = name
        parking.location = location
        parking.latitude = request.POST.get('latitude') or None
        parking.longitude = request.POST.get('longitude') or None
        parking.car_capacity = car_capacity
        parking.bike_capacity = bike_capacity
        parking.rate_per_hour = rate_per_hour
        parking.map_link = request.POST.get('map_link')
        parking.description = request.POST.get('description')
        parking.is_active = request.POST.get('is_active') == 'on'

        if request.FILES.get('parking_image'):
            parking.parking_image = request.FILES.get('parking_image')

        parking.save()
        messages.success(request, "Parking updated successfully.")
        return redirect('my_parking_lots')

    return render(request, 'edit_parking.html', {'parking': parking})

#delete
def delete_parking(request, parking_id):

    if request.session.get('role') != 'owner':
        return redirect('authentication')

    owner_user = Signup.objects.get(id=request.session['user_id'])
    profile = OwnerProfile.objects.get(owner=owner_user)

    parking = get_object_or_404(ParkingLot, id=parking_id, owner=profile)
    parking.delete()
    messages.success(request,"Parking deleted successfully.")
    return redirect('my_parking_lots')
#view
def view_parking(request, parking_id):

    parking = get_object_or_404(ParkingLot, id=parking_id, is_active=True)

    today = date.today().isoformat()

    car_booked = Booking.objects.filter(
        parking_name=parking.parking_name,
        vehicle_type='Car',
        booking_date=date.today(),
        status__in=['Pending', 'Active']
    ).count()

    bike_booked = Booking.objects.filter(
        parking_name=parking.parking_name,
        vehicle_type='Bike',
        booking_date=date.today(),
        status__in=['Pending', 'Active']
    ).count()

    reviews = parking.reviews.select_related('user').all()
    average_rating = parking.average_rating()
    review_count = parking.review_count()

    context = {
        'parking': parking,
        'today': today,
        'car_available': max(parking.car_capacity - car_booked, 0),
        'bike_available': max(parking.bike_capacity - bike_booked, 0),
        'logged_in': bool(request.session.get('user_id')),
        'reviews': reviews,
        'average_rating': average_rating,
        'review_count': review_count,
    }

    return render(request,'view_parking.html', context)


# Browse / Search Parking
def browse_parking(request):

    query = request.GET.get('q', '').strip()
    vehicle = request.GET.get('vehicle', '')
    sort = request.GET.get('sort', '')

    parkings = ParkingLot.objects.filter(is_active=True)

    if query:
        parkings = parkings.filter(location__icontains=query)

    if sort == 'price_low':
        parkings = parkings.order_by('rate_per_hour')
    elif sort == 'price_high':
        parkings = parkings.order_by('-rate_per_hour')
    else:
        parkings = parkings.order_by('-created_at')

    results = []
    today = date.today()

    saved_parking_ids = set()
    if request.session.get('user_id'):
        saved_parking_ids = set(
            SavedLocation.objects.filter(
                user_id=request.session['user_id']
            ).values_list('parking_id', flat=True)
        )

    for parking in parkings:

        car_booked = Booking.objects.filter(
            parking_name=parking.parking_name,
            vehicle_type='Car',
            booking_date=today,
            status__in=['Pending', 'Active']
        ).count()

        bike_booked = Booking.objects.filter(
            parking_name=parking.parking_name,
            vehicle_type='Bike',
            booking_date=today,
            status__in=['Pending', 'Active']
        ).count()

        car_available = max(parking.car_capacity - car_booked, 0)
        bike_available = max(parking.bike_capacity - bike_booked, 0)

        if vehicle == 'Car' and car_available <= 0:
            continue
        if vehicle == 'Bike' and bike_available <= 0:
            continue

        results.append({
            'parking': parking,
            'car_available': car_available,
            'bike_available': bike_available,
            'average_rating': parking.average_rating(),
            'review_count': parking.review_count(),
            'is_saved': parking.id in saved_parking_ids,
        })

    context = {
        'results': results,
        'query': query,
        'vehicle': vehicle,
        'sort': sort,
        'is_logged_in': bool(request.session.get('user_id')),
    }

    return render(request, 'browse_parking.html', context)


# TODO(owner): make dashboard compute owner revenue/occupancy from a single date-windowed aggregation so stats stay consistent with bookings.
def map_search(request):
    parkings = list(ParkingLot.objects.filter(is_active=True)[:200])
    sites = [
        {
            "id": p.id,
            "name": p.parking_name,
            "lat": float(p.latitude) if p.latitude else None,
            "lng": float(p.longitude) if p.longitude else None,
            "location": p.location,
            "rate_per_hour": float(p.rate_per_hour) if p.rate_per_hour else 0,
        }
        for p in parkings
    ]
    return render(request, "map_search.html", {"sites": sites})


# Create a Booking
def book_parking(request, parking_id):

    if not request.session.get('user_id'):
        messages.error(request, "Please login to book a parking spot.")
        return redirect('authentication')

    if request.session.get('role') != 'user':
        messages.error(request, "Only users can book parking spots.")
        return redirect('authentication')

    parking = get_object_or_404(ParkingLot, id=parking_id, is_active=True)

    if request.method != "POST":
        return redirect('view_parking', parking_id=parking.id)

    user = Signup.objects.get(id=request.session['user_id'])

    vehicle_number = request.POST.get('vehicle_number', '').strip()
    vehicle_type = request.POST.get('vehicle_type')
    booking_date_str = request.POST.get('booking_date')
    check_in_str = request.POST.get('check_in')
    check_out_str = request.POST.get('check_out')

    if not all([vehicle_number, vehicle_type, booking_date_str, check_in_str, check_out_str]):
        messages.error(request, "Please fill in all booking details.")
        return redirect('view_parking', parking_id=parking.id)

    try:
        booking_date = datetime.strptime(booking_date_str, '%Y-%m-%d').date()
        check_in = datetime.strptime(check_in_str, '%H:%M').time()
        check_out = datetime.strptime(check_out_str, '%H:%M').time()
    except ValueError:
        messages.error(request, "Invalid date or time format.")
        return redirect('view_parking', parking_id=parking.id)

    if booking_date < date.today():
        messages.error(request, "Booking date cannot be in the past.")
        return redirect('view_parking', parking_id=parking.id)

    check_in_minutes = check_in.hour * 60 + check_in.minute
    check_out_minutes = check_out.hour * 60 + check_out.minute

    if check_out_minutes <= check_in_minutes:
        messages.error(request, "Check-out time must be after check-in time.")
        return redirect('view_parking', parking_id=parking.id)

    duration = ceil((check_out_minutes - check_in_minutes) / 60)

    capacity = parking.car_capacity if vehicle_type == 'Car' else parking.bike_capacity

    existing_bookings = Booking.objects.filter(
        parking_name=parking.parking_name,
        vehicle_type=vehicle_type,
        booking_date=booking_date,
        status__in=['Pending', 'Active']
    ).count()

    if existing_bookings >= capacity:
        messages.error(
            request,
            f"Sorry, no {vehicle_type} slots available at this parking lot for the selected date."
        )
        return redirect('view_parking', parking_id=parking.id)

    amount = duration * parking.rate_per_hour

    Booking.objects.create(
        user=user,
        parking_name=parking.parking_name,
        vehicle_number=vehicle_number,
        vehicle_type=vehicle_type,
        booking_date=booking_date,
        check_in=check_in,
        check_out=check_out,
        duration=duration,
        amount=amount,
        payment_status='Unpaid',
        status='Pending'
    )

    booking = Booking.objects.filter(
        user=user, parking_name=parking.parking_name
    ).order_by('-created_at').first()

    messages.success(
        request,
        f"Booking created successfully. Please complete payment to confirm."
    )

    _notify_owner(
        parking,
        'booking',
        'New Booking Received',
        f"{vehicle_number} ({vehicle_type}) booked {parking.parking_name} on "
        f"{booking_date.strftime('%b %d, %Y')} from {check_in.strftime('%I:%M %p')} "
        f"to {check_out.strftime('%I:%M %p')}. Amount: Rs {amount}.",
        link=reverse('my_parking_lots'),
    )

    return redirect('payment_page', booking_id=booking.id)


# Cancel a Booking
def cancel_booking(request, booking_id):

    if not request.session.get('user_id'):
        return redirect('authentication')

    booking = get_object_or_404(
        Booking, id=booking_id, user_id=request.session['user_id']
    )

    if booking.status in ['Pending', 'Active']:
        booking.status = 'Cancelled'
        booking.save()
        messages.success(request, "Booking cancelled successfully.")
    else:
        messages.error(request, "This booking can no longer be cancelled.")

    return redirect('my_bookings')


# ---- Payment Module ----
# Note: No live gateway keys are configured in this project yet, so this
# simulates a payment gateway redirect/callback (Card / eSewa / Khalti style)
# end-to-end. Swap process_payment's internals for the real SDK call when
# you have merchant credentials, the rest of the flow stays the same.

def _generate_txn_id():
    return 'TXN' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))


def payment_page(request, booking_id):

    if not request.session.get('user_id'):
        return redirect('authentication')

    booking = get_object_or_404(
        Booking, id=booking_id, user_id=request.session['user_id']
    )

    if booking.payment_status == 'Paid':
        messages.success(request, "This booking is already paid.")
        return redirect('my_bookings')

    if booking.status == 'Cancelled':
        messages.error(request, "Cannot pay for a cancelled booking.")
        return redirect('my_bookings')

    return render(request, 'payment.html', {'booking': booking})


def process_payment(request, booking_id):

    if not request.session.get('user_id'):
        return redirect('authentication')

    booking = get_object_or_404(
        Booking, id=booking_id, user_id=request.session['user_id']
    )

    if request.method != "POST":
        return redirect('payment_page', booking_id=booking.id)

    if booking.payment_status == 'Paid':
        messages.success(request, "This booking is already paid.")
        return redirect('my_bookings')

    method = request.POST.get('method')

    if method not in ('Card', 'Esewa', 'Khalti'):
        messages.error(request, "Please select a valid payment method.")
        return redirect('payment_page', booking_id=booking.id)

    # ---- Simulated gateway validation ----
    if method == 'Card':
        card_number = request.POST.get('card_number', '').replace(' ', '')
        expiry = request.POST.get('expiry', '')
        cvv = request.POST.get('cvv', '')

        if len(card_number) < 12 or not card_number.isdigit():
            messages.error(request, "Enter a valid card number.")
            return redirect('payment_page', booking_id=booking.id)

        if not expiry or not cvv or len(cvv) < 3:
            messages.error(request, "Enter valid card expiry and CVV.")
            return redirect('payment_page', booking_id=booking.id)

    else:
        wallet_id = request.POST.get('wallet_id', '').strip()

        if not wallet_id:
            messages.error(request, f"Enter your {method} registered mobile number.")
            return redirect('payment_page', booking_id=booking.id)

    # In a real integration this is where you'd redirect to the gateway and
    # later receive a server-to-server callback confirming success/failure.
    # Here we mark it Success immediately to complete the simulated flow.
    txn = PaymentTransaction.objects.create(
        booking=booking,
        txn_id=_generate_txn_id(),
        method=method,
        amount=booking.amount,
        status='Success'
    )

    booking.payment_status = 'Paid'
    if booking.status == 'Pending':
        booking.status = 'Active'
    booking.save()

    messages.success(request, "Payment successful!")

    return redirect('payment_receipt', txn_id=txn.txn_id)


def payment_receipt(request, txn_id):

    if not request.session.get('user_id'):
        return redirect('authentication')

    txn = get_object_or_404(
        PaymentTransaction, txn_id=txn_id, booking__user_id=request.session['user_id']
    )

    return render(request, 'payment_receipt.html', {'txn': txn, 'booking': txn.booking})


# ---- Owner: manage bookings for their own parking lots ----

def owner_update_booking(request, booking_id):

    if request.session.get('role') != 'owner':
        return redirect('authentication')

    owner_user = Signup.objects.get(
        id=request.session['user_id']
    )

    owner_parking_names = list(
        ParkingLot.objects.filter(owner__owner=owner_user).values_list('parking_name', flat=True)
    )

    booking = get_object_or_404(
        Booking, id=booking_id, parking_name__in=owner_parking_names
    )

    new_status = request.POST.get('status') if request.method == "POST" else request.GET.get('status')

    valid_statuses = dict(Booking.STATUS_CHOICES)

    if new_status not in valid_statuses:
        messages.error(request, "Invalid booking status.")
        return redirect('owner_dashboard')

    booking.status = new_status
    booking.save()

    messages.success(
        request,
        f"Booking #{booking.id} marked as {new_status}."
    )

    return redirect('owner_dashboard')


def admin_toggle_parking(request, parking_id):

    if request.session.get('role') != 'admin':
        return redirect('authentication')

    parking = get_object_or_404(ParkingLot, id=parking_id)

    parking.is_active = not parking.is_active
    parking.save()

    messages.success(
        request,
        f"{parking.parking_name} is now {'Active' if parking.is_active else 'Inactive'}."
    )

    return redirect('admin_dashboard')


def admin_delete_parking(request, parking_id):

    if request.session.get('role') != 'admin':
        return redirect('authentication')

    parking = get_object_or_404(ParkingLot, id=parking_id)
    name = parking.parking_name
    parking.delete()

    messages.success(request, f"{name} has been removed by admin.")

    return redirect('admin_dashboard')


def admin_delete_review(request, review_id):

    if request.session.get('role') != 'admin':
        return redirect('authentication')

    review = get_object_or_404(Review, id=review_id)
    review.delete()

    messages.success(request, "Review removed by admin.")

    return redirect('admin_dashboard')
#my bookings
def my_bookings(request):

    if not request.session.get('user_id'):
        return redirect('authentication')

    bookings = Booking.objects.filter(
        user_id=request.session['user_id']
    ).select_related('review').order_by('-created_at')

    for booking in bookings:
        booking.existing_review = getattr(booking, 'review', None)
        booking.can_review = (
            booking.status == 'Completed' and booking.existing_review is None
        )

    return render(request,'my_bookings.html',{'bookings': bookings})
# ---- Review & Rating module ----

def _get_parking_for_booking(booking):
    """
    Booking stores the lot name as plain text rather than a FK, so resolve
    the actual ParkingLot row it refers to.
    """
    return ParkingLot.objects.filter(parking_name=booking.parking_name).first()


def submit_review(request, booking_id):

    if not request.session.get('user_id'):
        messages.error(request, "Please login to write a review.")
        return redirect('authentication')

    booking = get_object_or_404(
        Booking, id=booking_id, user_id=request.session['user_id']
    )

    if booking.status != 'Completed':
        messages.error(request, "You can only review completed bookings.")
        return redirect('my_bookings')

    if Review.objects.filter(booking=booking).exists():
        messages.error(request, "You have already reviewed this booking.")
        return redirect('my_bookings')

    parking = _get_parking_for_booking(booking)

    if not parking:
        messages.error(request, "This parking lot is no longer available.")
        return redirect('my_bookings')

    if request.method == "POST":

        rating = request.POST.get('rating')
        comment = request.POST.get('comment', '').strip()

        if rating not in ('1', '2', '3', '4', '5'):
            messages.error(request, "Please select a rating between 1 and 5.")
            return redirect('submit_review', booking_id=booking.id)

        Review.objects.create(
            parking=parking,
            user_id=request.session['user_id'],
            booking=booking,
            rating=int(rating),
            comment=comment,
        )

        _notify_owner(
            parking,
            'review',
            'New Review Received',
            f"{request.session.get('username', 'A user')} left a {rating}\u2605 "
            f"review on {parking.parking_name}"
            + (f": \"{comment}\"" if comment else "."),
            link=reverse('parking_reviews', args=[parking.id]),
        )

        messages.success(request, "Thank you! Your review has been posted.")
        return redirect('my_bookings')

    return render(
        request,
        'write_review.html',
        {'booking': booking, 'parking': parking, 'mode': 'create'}
    )


def edit_review(request, review_id):

    if not request.session.get('user_id'):
        return redirect('authentication')

    review = get_object_or_404(
        Review, id=review_id, user_id=request.session['user_id']
    )

    if request.method == "POST":

        rating = request.POST.get('rating')
        comment = request.POST.get('comment', '').strip()

        if rating not in ('1', '2', '3', '4', '5'):
            messages.error(request, "Please select a rating between 1 and 5.")
            return redirect('edit_review', review_id=review.id)

        review.rating = int(rating)
        review.comment = comment
        review.save()

        messages.success(request, "Your review has been updated.")
        return redirect('my_bookings')

    return render(
        request,
        'write_review.html',
        {
            'booking': review.booking,
            'parking': review.parking,
            'review': review,
            'mode': 'edit',
        }
    )


def delete_review(request, review_id):

    if not request.session.get('user_id'):
        return redirect('authentication')

    review = get_object_or_404(
        Review, id=review_id, user_id=request.session['user_id']
    )

    review.delete()

    messages.success(request, "Your review has been deleted.")
    return redirect('my_bookings')


# Owner: view & reply to reviews left for one of their parking lots
def parking_reviews(request, parking_id):

    if request.session.get('role') != 'owner':
        return redirect('authentication')

    owner_user = Signup.objects.get(id=request.session['user_id'])
    profile = OwnerProfile.objects.get(owner=owner_user)

    parking = get_object_or_404(ParkingLot, id=parking_id, owner=profile)

    reviews = parking.reviews.select_related('user').all()

    context = {
        'parking': parking,
        'reviews': reviews,
        'average_rating': parking.average_rating(),
        'review_count': parking.review_count(),
    }

    return render(request, 'owner_reviews.html', context)


def owner_reply_review(request, review_id):

    if request.session.get('role') != 'owner':
        return redirect('authentication')

    owner_user = Signup.objects.get(id=request.session['user_id'])
    profile = OwnerProfile.objects.get(owner=owner_user)

    review = get_object_or_404(Review, id=review_id, parking__owner=profile)

    if request.method == "POST":

        reply_text = request.POST.get('owner_reply', '').strip()

        if not reply_text:
            messages.error(request, "Reply cannot be empty.")
            return render(
                request,
                'owner_reviews.html',
                {
                    'parking': review.parking,
                    'reviews': review.parking.reviews.select_related('user').all(),
                    'average_rating': review.parking.average_rating(),
                    'review_count': review.parking.review_count(),
                    'mode': 'reply',
                },
            )

        review.owner_reply = reply_text
        review.replied_at = timezone.now()
        review.save()

        messages.success(request, "Your reply has been posted.")

    return redirect('parking_reviews', parking_id=review.parking.id)


# ---- Owner notifications ----

def mark_notification_read(request, notification_id):

    if request.session.get('role') != 'owner':
        return JsonResponse({'error': 'unauthorized'}, status=403)

    notif = get_object_or_404(
        Notification, id=notification_id, recipient_id=request.session['user_id']
    )

    notif.is_read = True
    notif.save(update_fields=['is_read'])

    return JsonResponse({'status': 'ok'})


def mark_all_notifications_read(request):

    if request.session.get('role') != 'owner':
        return JsonResponse({'error': 'unauthorized'}, status=403)

    Notification.objects.filter(
        recipient_id=request.session['user_id'], is_read=False
    ).update(is_read=True)

    return JsonResponse({'status': 'ok'})


def clear_notifications(request):

    if request.session.get('role') != 'owner':
        return JsonResponse({'error': 'unauthorized'}, status=403)

    Notification.objects.filter(recipient_id=request.session['user_id']).delete()

    return JsonResponse({'status': 'ok'})


# Logout
def logout_view(request):
    request.session.flush()
    messages.success(request, "Logged out successfully.")
    return redirect('authentication')


# Forgot Password (custom flow for the Signup model)

def forgot_password(request):

    if request.method == "POST":

        email = request.POST.get('email', '').strip()
        user = Signup.objects.filter(email=email).first()

        if user:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = signup_token_generator.make_token(user)

            reset_link = request.build_absolute_uri(
                f'/reset/{uid}/{token}/'
            )

            send_mail(
                subject="Reset your Parkify password",
                message=(
                    f"Hi {user.first_name},\n\n"
                    f"We received a request to reset your Parkify password. "
                    f"Click the link below to choose a new one:\n\n"
                    f"{reset_link}\n\n"
                    f"If you didn't request this, you can safely ignore this email."
                ),
                from_email=None,
                recipient_list=[user.email],
                fail_silently=True,
            )

        # Always show the same message, whether or not the email exists,so this can't be used to check which emails are registered.
        messages.success(
            request,
            "If an account exists for that email, a reset link has been sent."
        )

        return redirect('password_reset_done')

    return render(request, 'forgot_password.html')


def password_reset_done(request):
    return render(request, 'password_reset_done.html')


def reset_password_confirm(request, uidb64, token):

    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = Signup.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, Signup.DoesNotExist):
        user = None

    token_valid = user is not None and signup_token_generator.check_token(user, token)

    if not token_valid:
        messages.error(
            request,
            "This password reset link is invalid or has expired. Please request a new one."
        )
        return redirect('forgot_password')

    if request.method == "POST":

        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')

        if len(new_password) < 8:
            messages.error(request, "Password must be at least 8 characters long.")
            return render(request, 'reset_password.html', {'validlink': True})

        if new_password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, 'reset_password.html', {'validlink': True})

        user.password = make_password(new_password)
        user.save(update_fields=['password'])

        messages.success(request, "Your password has been reset successfully.")
        return redirect('password_reset_complete')

    return render(request, 'reset_password.html', {'validlink': True})


def password_reset_complete(request):
    return render(request, 'password_reset_complete.html')

# changing the password
def change_password(request):

    if not request.session.get('user_id'):
        return redirect('authentication')

    role = request.session.get('role')
    if role not in ('user', 'owner', 'admin'):
        return redirect('authentication')

    user = Signup.objects.get(id=request.session['user_id'])

    if request.method == "POST":
        current_password = request.POST.get("current_password", "")
        new_password = request.POST.get("new_password", "")
        confirm_password = request.POST.get("confirm_password", "")

        valid = False
        try:
            identify_hasher(user.password)
            valid = check_password(current_password, user.password)
        except ValueError:
            valid = user.password == current_password

        if not valid:
            messages.error(request, "Current password is incorrect.")
            return render(request, 'change_password.html', {
                'current_password': current_password
            })

        if new_password != confirm_password:
            messages.error(request, "New passwords do not match.")
            return render(request, 'change_password.html', {
                'current_password': current_password
            })

        if current_password == new_password:
            messages.error(request, "New password cannot be the same as the current password.")
            return render(request, 'change_password.html', {
                'current_password': current_password
            })

        if len(new_password) < 8:
            messages.error(request, "Password must be at least 8 characters long.")
            return render(request, 'change_password.html', {
                'current_password': current_password
            })

        user.password = make_password(new_password)
        user.save(update_fields=["password"])

        success_message = "Password changed successfully."
        dashboard_name = 'dashboard'
        if role == 'owner':
            success_message = "Owner password updated successfully."
            dashboard_name = 'owner_dashboard'
        elif role == 'admin':
            success_message = "Admin password updated successfully."
            dashboard_name = 'admin_dashboard'

        messages.success(request, success_message)

        try:
            send_mail(
                subject='Parkify password changed',
                message=f'Hi {user.username}, your password was changed successfully.',
                from_email=None,
                recipient_list=[user.email],
                fail_silently=True,
            )
        except Exception:
            pass

        return redirect(dashboard_name)

    return render(request, 'change_password.html')

# TODO(owner): expand SavedLocation endpoints after browse_parking map/bookmark UI is wired.
# TODO(owner): add optional password-change email notification when backend mail is configured.