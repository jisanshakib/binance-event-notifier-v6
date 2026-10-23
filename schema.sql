-- Run this in Supabase SQL Editor
create table if not exists source_channels (
  id bigserial primary key,
  username text unique not null,
  created_at timestamptz default now()
);
create table if not exists keywords (
  id bigserial primary key,
  word text unique not null,
  created_at timestamptz default now()
);
create table if not exists settings (
  key text primary key,
  value text
);
create table if not exists processed_posts (
  id bigserial primary key,
  source_chat text not null,
  message_id bigint not null,
  status text,
  output text,
  error text,
  created_at timestamptz default now(),
  unique (source_chat, message_id)
);
insert into source_channels(username) values ('binance_announcements'), ('binance_wallet_channel') on conflict do nothing;

-- The keywords table is left EMPTY on purpose.
-- The owner adds every keyword manually from Telegram with /addkw (and removes them with /delkw).
-- Until at least one keyword exists, the bot ignores every incoming post.
