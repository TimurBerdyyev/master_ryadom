"""Deleting an account (admin action).

Personal data goes away: the account, the master profile, services, work photos, avatar, reviews about
the master, notifications, subscription, timers for requests. Things other people own stay: clients'
orders keep existing (detached from the deleted master), complaints stay for the record without an author.
"""
from sqlalchemy.orm import Session

from app.models import (
    Address,
    Complaint,
    Master,
    MasterSubscription,
    Message,
    Notification,
    NotificationSettings,
    Order,
    OrderOffer,
    OrderStatus,
    Photo,
    RequestDelivery,
    Review,
    Service,
    SubscriptionPayment,
    User,
    WorkingHours,
)
from app.uploads import delete_upload

UNFINISHED = (OrderStatus.master_selected, OrderStatus.master_confirmed, OrderStatus.master_en_route,
              OrderStatus.in_progress)


def delete_user(db: Session, user: User) -> None:
    """Delete the user and everything personal; caller commits."""
    files = [user.photo] if user.photo else []
    master = db.query(Master).filter(Master.user_id == user.id).first()
    if master is not None:
        files += [p.url for p in db.query(Photo).filter(Photo.master_id == master.id)]
        for model in (Service, Photo, WorkingHours, OrderOffer, RequestDelivery, Review, MasterSubscription,
                      SubscriptionPayment):
            db.query(model).filter(model.master_id == master.id).delete(synchronize_session=False)
        # Clients' orders stay. Unfinished ones go back to "looking for a pro" (the admin sees them among
        # unanswered requests); finished ones just lose the deleted master.
        db.query(Order).filter(Order.master_id == master.id, Order.status.in_(UNFINISHED)).update(
            {Order.master_id: None, Order.status: OrderStatus.searching}, synchronize_session=False)
        db.query(Order).filter(Order.master_id == master.id).update({Order.master_id: None}, synchronize_session=False)
        db.delete(master)

    db.query(Order).filter(Order.client_id == user.id).update({Order.client_id: None}, synchronize_session=False)
    db.query(Review).filter(Review.client_id == user.id).update({Review.client_id: None}, synchronize_session=False)
    db.query(Complaint).filter(Complaint.author_id == user.id).update({Complaint.author_id: None}, synchronize_session=False)
    db.query(Complaint).filter(Complaint.target_user_id == user.id).update(
        {Complaint.target_user_id: None}, synchronize_session=False)
    for model in (Notification, NotificationSettings, Address):
        db.query(model).filter(model.user_id == user.id).delete(synchronize_session=False)
    db.query(Message).filter(Message.sender_id == user.id).delete(synchronize_session=False)
    db.delete(user)
    db.flush()

    for url in files:
        delete_upload(url)
