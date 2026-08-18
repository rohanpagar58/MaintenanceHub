from django.urls import path
from . import views

app_name = 'super_user'

urlpatterns = [
    path('',                                              views.login_page,              name='login_page'),
    path('login/',                                        views.login_view,              name='login'),
    path('logout/',                                       views.logout_view,             name='logout'),
    path('register-apartment/',                           views.register_apartment_page, name='register_apartment_page'),
    path('register-apartment/save/',                      views.register_apartment_view, name='register_apartment'),
    path('register-apartment/list/',                      views.get_apartments_list_view, name='get_apartments_list'),
    path('register-apartment/update/<str:society_id>/',   views.update_apartment_view,   name='update_apartment'),
    path('register-apartment/check-duplicate/',           views.check_duplicate_view,    name='check_duplicate'),
    path('register-apartment/delete/<str:society_id>/',   views.delete_apartment_view,   name='delete_apartment'),
    path('register-apartment/toggle-status/<str:society_id>/', views.toggle_status_view, name='toggle_status'),
    path('user/home/',                                         views.user_home_view,       name='user_home'),
    path('user/profile/',                                      views.user_profile_view,    name='user_profile'),
    path('user/members/add/',                                  views.add_member_page,      name='add_member_page'),
    path('user/members/save/',                                 views.add_member_view,      name='add_member'),
    path('user/members/delete/<str:member_id>/',               views.delete_member_view,   name='delete_member'),
]

