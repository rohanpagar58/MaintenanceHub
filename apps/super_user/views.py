import json
import re
import datetime
from functools import wraps

from bson import ObjectId
from bson.errors import InvalidId
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib.auth.hashers import make_password, check_password
from django.views.decorators.http import require_http_methods
from django.conf import settings
from pymongo import MongoClient, ASCENDING, ReturnDocument
from pymongo.errors import PyMongoError, DuplicateKeyError


EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


def normalize_mobile(mobile):
    """Strip spaces, dashes and parens from a mobile number for comparison/storage."""
    return mobile.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')


def validate_mobile(mobile):
    """Return an error string if the mobile number is invalid, else None."""
    digits = normalize_mobile(mobile)
    clean = digits[1:] if digits.startswith('+') else digits
    if not clean.isdigit():
        return 'Mobile number must contain only digits, spaces, dashes or a leading +.'
    if not (7 <= len(clean) <= 15):
        return 'Mobile number must be 7–15 digits.'
    if not digits.startswith('+') and not digits.startswith('91') and len(clean) != 10:
        return 'Indian mobile numbers must be exactly 10 digits.'
    return None


# ─────────────────────────────────────────────
#  MongoDB helper
# ─────────────────────────────────────────────

def get_mongo_db():
    """Return the PDF database client."""
    client = MongoClient(
        getattr(settings, 'MONGO_URI', 'mongodb://localhost:27017/'),
        serverSelectionTimeoutMS=3000
    )
    db_name = getattr(settings, 'MONGO_DB_NAME', 'PDF')
    return client[db_name]


def get_mongo_collection():
    """Return the companies collection from PDF database."""
    return get_mongo_db()['companies']


def get_user_collection():
    """Return the 'user' collection that stores society members."""
    return get_mongo_db()['user']


# ─────────────────────────────────────────────
#  Index bootstrapping (runs once per process)
# ─────────────────────────────────────────────

_indexes_ensured = False

def ensure_indexes():
    """
    Idempotently create indexes on the companies collection for fast lookups.
    - chairman_email: regular index (for fast duplicate checks & searches).
    - chairman_mobile: regular index (for fast duplicate checks & searches).
    - society_id: unique index (IDs must truly be unique).
    Runs at most once per Django process lifetime.
    """
    global _indexes_ensured
    if _indexes_ensured:
        return
    try:
        col = get_mongo_collection()
        # Regular indexes — improve query speed, uniqueness enforced in code
        col.create_index(
            [('chairman_email', ASCENDING)],
            name='idx_chairman_email',
            background=True,
        )
        col.create_index(
            [('chairman_mobile', ASCENDING)],
            name='idx_chairman_mobile',
            background=True,
        )
        # society_id must be truly unique at DB level
        col.create_index(
            [('society_id', ASCENDING)],
            unique=True,
            name='idx_society_id_unique',
            background=True,
        )
        _indexes_ensured = True

        # ── user collection indexes ──────────────────────────────────────────
        user_col = get_user_collection()

        # Most queries filter by society_id (fetch all members of a society)
        user_col.create_index(
            [('society_id', ASCENDING)],
            name='idx_user_society_id',
            background=True,
        )
        # Compound: society_id + flat_number → uniqueness check & per-flat lookups
        user_col.create_index(
            [('society_id', ASCENDING), ('flat_number', ASCENDING)],
            unique=True,
            name='idx_user_society_flat_unique',
            background=True,
        )
        # Name search / sort within a society
        user_col.create_index(
            [('society_id', ASCENDING), ('name', ASCENDING)],
            name='idx_user_society_name',
            background=True,
        )

    except Exception:
        pass  # Non-fatal — code-level checks still enforce uniqueness


# ─────────────────────────────────────────────
#  Super-user session decorator
# ─────────────────────────────────────────────

def super_user_required(view_func):
    """Redirect to login if the user is not authenticated as super_user."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('is_super_user'):
            return redirect('/auth/')
        return view_func(request, *args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────
#  Login
# ─────────────────────────────────────────────

def login_page(request):
    """Render the login page."""
    if request.session.get('is_super_user'):
        return redirect('/auth/register-apartment/')
    return render(request, 'auth/login.html')



@require_http_methods(["POST"])
def login_view(request):
    """
    Handle login form submission.
    Checks:
      1. Super-user record (field: email)
      2. Apartment chairman record (field: chairman_email + is_active guard)
    """
    try:
        data     = json.loads(request.body)
        email    = data.get('email', '').strip().lower()
        password = data.get('password', '').strip()

        if not email or not password:
            return JsonResponse({'success': False, 'error': 'Email and password are required.'}, status=400)

        collection = get_mongo_collection()

        # ── 1. Try super-user account (stored as 'email') ──────────────────────────
        company = collection.find_one({'email': email})
        if company:
            stored_password = company.get('password', '')
            if not check_password(password, stored_password) and stored_password != password:
                return JsonResponse({'success': False, 'error': 'Invalid password.'}, status=401)

            # Status check
            if company.get('is_active') is False:
                return JsonResponse(
                    {'success': False, 'error': 'This account has been deactivated. Please contact the administrator.'},
                    status=403
                )

            # Set super_user session
            request.session['is_super_user']    = True
            request.session['super_user_email'] = email
            request.session['super_user_name']  = company.get('name', email)

            return JsonResponse({
                'success':  True,
                'message':  f"Welcome, {company.get('name', email)}!",
                'redirect': '/auth/register-apartment/',
            })

        # ── 2. Try apartment chairman account (stored as 'chairman_email') ───────────
        apartment = collection.find_one({'chairman_email': email, 'society_id': {'$exists': True}})
        if apartment:
            stored_password = apartment.get('password', '')
            if not check_password(password, stored_password) and stored_password != password:
                return JsonResponse({'success': False, 'error': 'Invalid password.'}, status=401)

            # Status / active check
            if apartment.get('is_active') is False:
                return JsonResponse(
                    {'success': False, 'error': 'Your society account has been deactivated. Please contact the administrator.'},
                    status=403
                )

            # Subscription check
            sub_valid_upto = apartment.get('subscription_valid_upto')
            if sub_valid_upto:
                try:
                    sub_date = datetime.date.fromisoformat(sub_valid_upto)
                    if sub_date < datetime.date.today():
                        # Deactivate account if subscription expired
                        collection.update_one(
                            {'_id': apartment['_id']},
                            {'$set': {'is_active': False}}
                        )
                        return JsonResponse(
                            {'success': False, 'error': 'Your subscription has ended. Please renew to continue.'},
                            status=403
                        )
                except ValueError:
                    pass

            # Set apartment user session
            request.session['is_apartment_user']  = True
            request.session['apartment_email']     = email
            request.session['apartment_society']   = apartment.get('society_name', '')
            request.session['apartment_society_id']= apartment.get('society_id', '')
            request.session['apartment_block']     = apartment.get('block', '')
            request.session['financial_year_start_month'] = apartment.get('financial_year_start_month', 4)

            society_display = apartment.get('society_name', 'Your Society')
            return JsonResponse({
                'success':  True,
                'message':  f"Welcome back! {society_display}",
                'redirect': '/auth/user/home/',
            })

        # ── 3. No matching account ─────────────────────────────────────────────────
        return JsonResponse({'success': False, 'error': 'No account found with this email.'}, status=401)

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid request data.'}, status=400)
    except PyMongoError:
        return JsonResponse({'success': False, 'error': 'Database connection error. Please ensure MongoDB service is running.'}, status=500)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)


# ─────────────────────────────────────────────
#  Logout
# ─────────────────────────────────────────────

def logout_view(request):
    """Clear all sessions and redirect to login."""
    request.session.flush()
    return redirect('/auth/')


# ─────────────────────────────────────────────
#  Apartment User session decorator
# ─────────────────────────────────────────────

def apartment_user_required(view_func):
    """Redirect to login if the user is not authenticated as an apartment user."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('is_apartment_user'):
            return redirect('/auth/')
        return view_func(request, *args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────
#  User Home Page (apartment chairman)
# ─────────────────────────────────────────────

@apartment_user_required
@require_http_methods(["GET"])
def user_home_view(request):
    """Render the home page for a logged-in apartment chairman."""
    society_id = request.session.get('apartment_society_id', '')
    collection  = get_mongo_collection()
    apartment   = collection.find_one({'society_id': society_id}) if society_id else None

    # Fetch all members for this society, sorted by flat number
    user_col = get_user_collection()
    members_cursor = user_col.find({'society_id': society_id}).sort('flat_number', ASCENDING)
    members = [
        {
            'id':          str(m['_id']),
            'index_id':    m.get('index_id', ''),
            'name':        m.get('name', ''),
            'flat_number': m.get('flat_number', ''),
        }
        for m in members_cursor
    ]

    context = {
        'society_name':            apartment.get('society_name', '') if apartment else request.session.get('apartment_society', ''),
        'apartment_block':         apartment.get('block', '') if apartment else request.session.get('apartment_block', ''),
        'total_flats':             apartment.get('total_flats', 0) if apartment else 0,
        'subscription_valid_upto': apartment.get('subscription_valid_upto', '') if apartment else '',
        'society_id':              society_id,
        'members':                 members,
    }
    return render(request, 'user/home.html', context)

@apartment_user_required
@require_http_methods(["GET"])
def user_profile_view(request):
    """Render the profile page for a logged-in apartment chairman."""
    society_id = request.session.get('apartment_society_id', '')
    collection  = get_mongo_collection()
    apartment   = collection.find_one({'society_id': society_id}) if society_id else None

    context = {
        'society_name':            apartment.get('society_name', '') if apartment else request.session.get('apartment_society', ''),
        'apartment_block':         apartment.get('block', '') if apartment else request.session.get('apartment_block', ''),
        'address':                 apartment.get('address', '') if apartment else '',
        'chairman_email':          apartment.get('chairman_email', '') if apartment else request.session.get('apartment_email', ''),
        'chairman_mobile':         apartment.get('chairman_mobile', '') if apartment else '',
        'total_flats':             apartment.get('total_flats', 0) if apartment else 0,
        'subscription_valid_upto': apartment.get('subscription_valid_upto', '') if apartment else '',
        'society_id':              society_id,
    }
    return render(request, 'user/profile.html', context)


# ─────────────────────────────────────────────
#  Register Apartment (super_user only)
# ─────────────────────────────────────────────

@super_user_required
def register_apartment_page(request):
    """Render the Register Apartment page."""
    return render(request, 'super_user/register_apartment.html', {
        'super_user_name': request.session.get('super_user_name', 'Super User'),
    })


@super_user_required
@require_http_methods(["POST"])
def register_apartment_view(request):
    """
    Save a new apartment entry to the companies collection in MongoDB.
    Fields saved: society_id (auto-generated), society_name, block, chairman_email, registered_at.
    """
    try:
        data            = json.loads(request.body)
        society_name    = data.get('society_name', '').strip()
        block           = data.get('block', '').strip()         # optional
        address         = data.get('address', '').strip()       # optional
        chairman_email  = data.get('chairman_email', '').strip().lower()
        chairman_mobile = data.get('chairman_mobile', '').strip()   # optional
        total_flats     = data.get('total_flats', 0)
        subscription_valid_upto = data.get('subscription_valid_upto', '').strip()
        financial_year_start_month = data.get('financial_year_start_month', '4') # Default to April
        signature_image = data.get('signature_image', '')
        password        = data.get('password', '').strip()

        # ── Required field checks ──────────────────────────────────────────
        if not society_name:
            return JsonResponse({'success': False, 'error': 'Society / Company Name is required.'}, status=400)

        if not chairman_email:
            return JsonResponse({'success': False, 'error': 'Chairman Email is required.'}, status=400)

        if not EMAIL_RE.match(chairman_email):
            return JsonResponse({'success': False, 'error': 'Chairman Email format is invalid.'}, status=400)

        # ── Optional: Mobile validation (if provided) ──────────────────────
        if chairman_mobile:
            mobile_error = validate_mobile(chairman_mobile)
            if mobile_error:
                return JsonResponse({'success': False, 'error': mobile_error}, status=400)

        # ── Total flats ────────────────────────────────────────────────────
        try:
            total_flats_int = int(total_flats)
            if total_flats_int < 1:
                raise ValueError
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'Total Flats must be a whole number ≥ 1.'}, status=400)

        # ── Subscription date ──────────────────────────────────────────────
        if not subscription_valid_upto:
            return JsonResponse({'success': False, 'error': 'Subscription Valid Upto date is required.'}, status=400)
        try:
            sub_date = datetime.date.fromisoformat(subscription_valid_upto)
            if sub_date < datetime.date.today():
                return JsonResponse({'success': False, 'error': 'Subscription Valid Upto date cannot be in the past.'}, status=400)
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Subscription Valid Upto has an invalid date format (expected YYYY-MM-DD).'}, status=400)

        # ── Password ───────────────────────────────────────────────────────
        if not password or len(password) < 6:
            return JsonResponse({'success': False, 'error': 'Password must be at least 6 characters.'}, status=400)

        collection = get_mongo_collection()
        ensure_indexes()  # no-op after first call

        # ── Uniqueness checks ──────────────────────────────────────────────
        if collection.find_one({'chairman_email': chairman_email}):
            return JsonResponse(
                {'success': False, 'error': f'The email "{chairman_email}" is already registered with another society.'},
                status=409
            )

        if chairman_mobile:
            # Normalise to digits-only for the duplicate check
            norm_mobile = normalize_mobile(chairman_mobile)
            if collection.find_one({'chairman_mobile': {'$in': [chairman_mobile, norm_mobile]}}):
                return JsonResponse(
                    {'success': False, 'error': f'The mobile number "{chairman_mobile}" is already registered with another society.'},
                    status=409
                )

        # Auto-generate unique society_id (e.g. 001, 002, ...)
        count = collection.count_documents({}) + 1
        society_id = f"{count:03d}"
        while collection.find_one({'society_id': society_id}):
            count += 1
            society_id = f"{count:03d}"

        apartment_doc = {
            'society_id':              society_id,
            'society_name':            society_name,
            'block':                   block,
            'address':                 address,
            'chairman_email':          chairman_email,
            'chairman_mobile':         chairman_mobile,
            'total_flats':             total_flats_int,
            'subscription_valid_upto': subscription_valid_upto,
            'financial_year_start_month': int(financial_year_start_month),
            'signature_image':         signature_image,
            'password':                make_password(password),
            'is_active':               True,
            'registered_at':           datetime.datetime.utcnow().isoformat(),
        }

        result = collection.insert_one(apartment_doc)

        return JsonResponse({
            'success': True,
            'message': f'Apartment in {block}, {society_name} (ID: {society_id}) registered successfully!',
            'id':      str(result.inserted_id),
            'society_id': society_id,
            'apartment': {
                'id': str(result.inserted_id),
                'society_id': society_id,
                'society_name': society_name,
                'block': block,
                'address': address,
                'chairman_email': chairman_email,
                'chairman_mobile': chairman_mobile,
                'total_flats': total_flats_int,
                'subscription_valid_upto': subscription_valid_upto,
                'registered_at': apartment_doc['registered_at'],
                'signature_image': signature_image,
            }
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid request data.'}, status=400)
    except DuplicateKeyError as e:
        err_str = str(e)
        if 'chairman_email' in err_str:
            return JsonResponse({'success': False, 'error': 'That email address is already registered.'}, status=409)
        if 'chairman_mobile' in err_str:
            return JsonResponse({'success': False, 'error': 'That mobile number is already registered.'}, status=409)
        if 'society_id' in err_str:
            return JsonResponse({'success': False, 'error': 'Society ID collision — please try again.'}, status=409)
        return JsonResponse({'success': False, 'error': 'A duplicate entry was detected.'}, status=409)
    except PyMongoError:
        return JsonResponse({'success': False, 'error': 'Database connection error. Please ensure MongoDB service is running.'}, status=500)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)


@super_user_required
@require_http_methods(["GET"])
def get_apartments_list_view(request):
    """
    Fetch all registered apartments from MongoDB companies collection.
    Also returns aggregated stats and unique society names for dynamic features.
    """
    try:
        collection = get_mongo_collection()

        # Single fetch of every registered apartment; stats are always computed
        # over the full set, and the optional search filter narrows the list in Python.
        all_docs = list(collection.find({'society_id': {'$exists': True}}))

        unique_societies = set()
        unique_chairmen = set()
        for doc in all_docs:
            soc_name = doc.get('society_name', '')
            ch_email = doc.get('chairman_email', '')
            if soc_name:
                unique_societies.add(soc_name)
            if ch_email:
                unique_chairmen.add(ch_email)

        search_q = request.GET.get('q', '').strip()
        if search_q:
            regex_pat = re.compile(re.escape(search_q), re.IGNORECASE)
            search_fields = ('society_name', 'block', 'address', 'chairman_email', 'society_id')
            matching_docs = [
                doc for doc in all_docs
                if any(regex_pat.search(str(doc.get(field, ''))) for field in search_fields)
            ]
        else:
            matching_docs = all_docs

        apartments = [
            {
                'id': str(doc['_id']),
                'society_id': doc.get('society_id', ''),
                'society_name': doc.get('society_name', ''),
                'block': doc.get('block', ''),
                'address': doc.get('address', ''),
                'chairman_email': doc.get('chairman_email', ''),
                'chairman_mobile': doc.get('chairman_mobile', ''),
                'total_flats': doc.get('total_flats', 0),
                'subscription_valid_upto': doc.get('subscription_valid_upto', ''),
                'registered_at': doc.get('registered_at', ''),
                'is_active': doc.get('is_active', True),  # default True for old records
                'signature_image': doc.get('signature_image', ''),
            }
            for doc in matching_docs
        ]

        # Sort apartments by society_id / registered_at descending
        apartments.sort(key=lambda x: x.get('registered_at', '') or x.get('society_id', ''), reverse=True)

        stats = {
            'total_apartments': len(all_docs),
            'total_societies': len(unique_societies),
            'unique_chairmen': len(unique_chairmen),
        }

        return JsonResponse({
            'success': True,
            'apartments': apartments,
            'stats': stats,
            'societies': sorted(list(unique_societies)),
        })
    except PyMongoError:
        return JsonResponse({'success': False, 'error': 'Database error while fetching apartments.'}, status=500)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)


@super_user_required
@require_http_methods(["POST", "PUT"])
def update_apartment_view(request, society_id):
    """
    Update an existing apartment entry in the MongoDB companies collection.
    """
    try:
        data            = json.loads(request.body)
        society_name    = data.get('society_name', '').strip()
        block           = data.get('block', '').strip()
        address         = data.get('address', '').strip()
        chairman_email  = data.get('chairman_email', '').strip().lower()
        chairman_mobile = data.get('chairman_mobile', '').strip()
        total_flats     = data.get('total_flats', 0)
        subscription_valid_upto = data.get('subscription_valid_upto', '').strip()
        signature_image = data.get('signature_image', '')
        password        = data.get('password', '').strip()

        # ── Required field checks ──────────────────────────────────────────
        if not society_name:
            return JsonResponse({'success': False, 'error': 'Society / Company Name is required.'}, status=400)
        if not chairman_email:
            return JsonResponse({'success': False, 'error': 'Chairman Email is required.'}, status=400)
        if not EMAIL_RE.match(chairman_email):
            return JsonResponse({'success': False, 'error': 'Chairman Email format is invalid.'}, status=400)

        # ── Optional: Mobile validation (if provided) ──────────────────────
        if chairman_mobile:
            mobile_error = validate_mobile(chairman_mobile)
            if mobile_error:
                return JsonResponse({'success': False, 'error': mobile_error}, status=400)

        # ── Total flats ────────────────────────────────────────────────────
        try:
            total_flats_int = int(total_flats)
            if total_flats_int < 1:
                raise ValueError
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'Total Flats must be a whole number ≥ 1.'}, status=400)

        # ── Subscription date ──────────────────────────────────────────────
        if not subscription_valid_upto:
            return JsonResponse({'success': False, 'error': 'Subscription Valid Upto date is required.'}, status=400)

        collection = get_mongo_collection()
        doc = collection.find_one({'society_id': society_id})
        if not doc:
            return JsonResponse({'success': False, 'error': 'Apartment record not found.'}, status=404)

        # ── Uniqueness checks (excluding current doc) ───────────────────────
        if chairman_email != doc.get('chairman_email'):
            if collection.find_one({'chairman_email': chairman_email}):
                return JsonResponse({'success': False, 'error': f'The email "{chairman_email}" is already registered with another society.'}, status=409)

        if chairman_mobile and chairman_mobile != doc.get('chairman_mobile'):
            norm_mobile = normalize_mobile(chairman_mobile)
            existing_mobile = collection.find_one({
                'society_id': {'$ne': society_id},
                'chairman_mobile': {'$in': [chairman_mobile, norm_mobile]}
            })
            if existing_mobile:
                return JsonResponse({'success': False, 'error': f'The mobile number "{chairman_mobile}" is already registered with another society.'}, status=409)

        update_data = {
            'society_name':            society_name,
            'block':                   block,
            'address':                 address,
            'chairman_email':          chairman_email,
            'chairman_mobile':         chairman_mobile,
            'total_flats':             total_flats_int,
            'subscription_valid_upto': subscription_valid_upto,
            'signature_image':         signature_image,
        }

        # Update password only if provided
        if password:
            if len(password) < 6:
                return JsonResponse({'success': False, 'error': 'Password must be at least 6 characters.'}, status=400)
            update_data['password'] = make_password(password)

        collection.update_one({'society_id': society_id}, {'$set': update_data})

        return JsonResponse({
            'success': True,
            'message': 'Apartment updated successfully!',
            'society_id': society_id
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid request data.'}, status=400)
    except PyMongoError:
        return JsonResponse({'success': False, 'error': 'Database error during update.'}, status=500)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)


@super_user_required
@require_http_methods(["GET"])
def check_duplicate_view(request):
    """
    Check if an apartment with the specified society_name and block, email, or mobile already exists.
    """
    try:
        society_name = request.GET.get('society_name', '').strip()
        block = request.GET.get('block', '').strip()
        email = request.GET.get('email', '').strip().lower()
        mobile = request.GET.get('mobile', '').strip()

        collection = get_mongo_collection()
        duplicates = []

        # Check Society Name + Block
        if society_name and block:
            soc_pattern = re.compile(f"^{re.escape(society_name)}$", re.IGNORECASE)
            block_pattern = re.compile(f"^{re.escape(block)}$", re.IGNORECASE)
            existing_apt = collection.find_one({
                'society_name': soc_pattern,
                'block': block_pattern,
                'society_id': {'$exists': True}
            })
            if existing_apt:
                duplicates.append({
                    'field': 'block', 
                    'id': existing_apt.get('society_id'), 
                    'message': f'Block "{block}" is already registered under "{society_name}"'
                })

        # Check Email
        if email:
            existing_email = collection.find_one({'chairman_email': email, 'society_id': {'$exists': True}})
            if existing_email:
                duplicates.append({
                    'field': 'email', 
                    'id': existing_email.get('society_id'), 
                    'message': f'Email "{email}" is already registered'
                })
                
        # Check Mobile
        if mobile:
            norm_mobile = normalize_mobile(mobile)
            existing_mobile = collection.find_one({
                'chairman_mobile': {'$in': [mobile, norm_mobile]}, 
                'society_id': {'$exists': True}
            })
            if existing_mobile:
                duplicates.append({
                    'field': 'mobile', 
                    'id': existing_mobile.get('society_id'), 
                    'message': f'Mobile "{mobile}" is already registered'
                })

        return JsonResponse({
            'success': True,
            'duplicates': duplicates
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@super_user_required
@require_http_methods(["DELETE", "POST"])
def delete_apartment_view(request, society_id):
    """
    Delete an apartment entry from MongoDB by society_id.
    """
    try:
        collection = get_mongo_collection()
        result = collection.delete_one({'society_id': society_id})

        if result.deleted_count > 0:
            return JsonResponse({
                'success': True,
                'message': f'Apartment ID {society_id} deleted successfully.',
                'society_id': society_id,
            })
        else:
            return JsonResponse({'success': False, 'error': 'Apartment not found.'}, status=404)
    except PyMongoError:
        return JsonResponse({'success': False, 'error': 'Database error during deletion.'}, status=500)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)


# ─────────────────────────────────────────────
#  Toggle Active Status
# ─────────────────────────────────────────────

@super_user_required
@require_http_methods(["POST"])
def toggle_status_view(request, society_id):
    """
    Toggle is_active flag for a registered apartment.
    Active -> Inactive: blocks the chairman from logging in.
    Inactive -> Active: restores login access.
    """
    try:
        collection = get_mongo_collection()
        doc = collection.find_one({'society_id': society_id})

        if not doc:
            return JsonResponse({'success': False, 'error': 'Record not found.'}, status=404)

        # Flip the status (default True for legacy records without the field)
        current_status = doc.get('is_active', True)
        new_status = not current_status

        collection.update_one(
            {'society_id': society_id},
            {'$set': {'is_active': new_status}}
        )

        status_label = 'Active' if new_status else 'Inactive'
        return JsonResponse({
            'success': True,
            'society_id': society_id,
            'is_active': new_status,
            'message': f'Account status updated to {status_label}.',
        })
    except PyMongoError:
        return JsonResponse({'success': False, 'error': 'Database error during status update.'}, status=500)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)


# ─────────────────────────────────────────────
#  Member Management  (society chairman)
# ─────────────────────────────────────────────

def get_next_member_id():
    """
    Atomically increment and return the next integer member ID.
    Uses a 'counters' collection with a single document keyed 'member_id'.
    Sequence: 1, 2, 3, … ∞
    """
    counters = get_mongo_db()['counters']
    result = counters.find_one_and_update(
        {'_id': 'member_id'},
        {'$inc': {'seq': 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,   # return the updated document
    )
    return result['seq']


@apartment_user_required
@require_http_methods(["GET"])
def add_member_page(request):
    """Render the Add Member form."""
    society_id = request.session.get('apartment_society_id', '')
    collection  = get_mongo_collection()
    apartment   = collection.find_one({'society_id': society_id}) if society_id else None

    return render(request, 'user/add_member.html', {
        'society_name': apartment.get('society_name', '') if apartment else '',
        'society_id':   society_id,
    })


@apartment_user_required
@require_http_methods(["POST"])
def add_member_view(request):
    """Save a new member to the 'user' collection with an auto-increment integer _id."""
    society_id  = request.session.get('apartment_society_id', '')
    name        = request.POST.get('name', '').strip()
    flat_number = request.POST.get('flat_number', '').strip()

    if not society_id:
        return JsonResponse({'success': False, 'error': 'Session expired. Please log in again.'}, status=401)

    if not name:
        return JsonResponse({'success': False, 'error': 'Member name is required.'}, status=400)

    if not flat_number:
        return JsonResponse({'success': False, 'error': 'Flat number is required.'}, status=400)

    try:
        user_col = get_user_collection()

        # Check if flat already has a member registered
        existing = user_col.find_one({'society_id': society_id, 'flat_number': flat_number})
        if existing:
            return JsonResponse({
                'success': False,
                'error': f'Flat {flat_number} already has a registered member: {existing.get("name", "")}.'
            }, status=400)

        # Get next auto-increment sequential index
        next_index = get_next_member_id()

        member = {
            'index_id':     next_index,       # 1, 2, 3, 4 ... (human-readable)
            'society_id':   society_id,
            'name':         name,
            'flat_number':  flat_number,
            'created_at':   datetime.datetime.utcnow().isoformat(),
        }
        result = user_col.insert_one(member)
        new_oid = str(result.inserted_id)

        return JsonResponse({
            'success':       True,
            'message':       f'Member "{name}" added to flat {flat_number}.',
            'member_id':     next_index,     # sequential number shown in # column
            'member_id_str': new_oid,        # ObjectId string used for delete
        })

    except PyMongoError as e:
        return JsonResponse({'success': False, 'error': f'Database error: {str(e)}'}, status=500)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)


@apartment_user_required
@require_http_methods(["DELETE"])
def delete_member_view(request, member_id):
    """Delete a member from the 'user' collection by their MongoDB _id string."""
    society_id = request.session.get('apartment_society_id', '')

    try:
        oid = ObjectId(member_id)
    except InvalidId:
        return JsonResponse({'success': False, 'error': 'Invalid member ID.'}, status=400)

    try:
        user_col = get_user_collection()
        # Ensure the member belongs to this society (security check)
        result = user_col.delete_one({'_id': oid, 'society_id': society_id})
        if result.deleted_count == 0:
            return JsonResponse({'success': False, 'error': 'Member not found or access denied.'}, status=404)
        return JsonResponse({'success': True, 'message': 'Member deleted successfully.'})

    except PyMongoError as e:
        return JsonResponse({'success': False, 'error': f'Database error: {str(e)}'}, status=500)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)
