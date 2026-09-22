from alembic import op
import sqlalchemy as sa


revision = '3b8e6d14c5f7'
down_revision = '2a7c4e91b6d2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'provider_credentials',
        sa.Column('provider_id', sa.Integer(), nullable=True),
    )

    op.create_index(
        'ix_provider_credentials_provider_id',
        'provider_credentials',
        ['provider_id'],
        unique=False,
    )

    op.create_foreign_key(
        'fk_provider_credentials_provider_id',
        'provider_credentials',
        'providers',
        ['provider_id'],
        ['id'],
        ondelete='SET NULL',
    )

    op.execute(
        sa.text("""
            UPDATE provider_credentials pc
            SET provider_id = p.id
            FROM providers p
            WHERE pc.provider_id IS NULL
              AND pc.provider = p.key
        """)
    )


def downgrade():
    op.drop_constraint(
        'fk_provider_credentials_provider_id',
        'provider_credentials',
        type_='foreignkey',
    )
    op.drop_index(
        'ix_provider_credentials_provider_id',
        table_name='provider_credentials',
    )
    op.drop_column('provider_credentials', 'provider_id')
