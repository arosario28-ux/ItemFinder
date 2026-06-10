-- Supabase schema for ItemFinder

create extension if not exists pgcrypto;

create table if not exists public.items (
    id text primary key,
    item_type text not null check (item_type in ('lost', 'found')),
    title text not null,
    description text,
    category text,
    location text,
    date_occurred date,
    date_posted timestamptz not null default now(),
    contact_name text,
    contact_email text,
    contact_phone text,
    photo_id text,
    status text not null default 'open' check (status in ('open', 'resolved'))
);

create index if not exists idx_items_type_status_posted
    on public.items (item_type, status, date_posted desc);

create table if not exists public.dev_users (
    id text primary key,
    email text not null unique,
    name text,
    created_at timestamptz not null default now()
);

create index if not exists idx_dev_users_email on public.dev_users (email);

create table if not exists public.auction_bids (
    id text primary key,
    item_id text not null references public.items(id) on delete cascade,
    first_name text not null,
    last_name text not null,
    email text not null,
    bid_amount numeric(10,2) not null check (bid_amount > 0),
    created_at timestamptz not null default now(),
    unique (item_id, email)
);

create index if not exists idx_auction_bids_item_created
    on public.auction_bids (item_id, created_at);
