"""one offer per master per order, enforced by the database

Two offers sent by the same master at the same moment could both be inserted. Duplicates that may already
exist are removed first (the newest one stays: it carries the master's latest price).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-15

"""
from alembic import op


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM order_offers WHERE id NOT IN "
        "(SELECT max_id FROM (SELECT MAX(id) AS max_id FROM order_offers GROUP BY order_id, master_id) AS keep)"
    )
    with op.batch_alter_table('order_offers', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_offer_order_master', ['order_id', 'master_id'])


def downgrade() -> None:
    with op.batch_alter_table('order_offers', schema=None) as batch_op:
        batch_op.drop_constraint('uq_offer_order_master', type_='unique')
