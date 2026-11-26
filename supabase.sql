create table if not exists public.books (
  id text primary key,
  owner uuid not null references auth.users (id) on delete cascade,
  book jsonb not null,
  updated_at timestamptz not null default now()
);

alter table public.books enable row level security;

grant select, insert, update, delete on table public.books to authenticated;

drop policy if exists "Users can read their own books" on public.books;
create policy "Users can read their own books"
  on public.books for select
  using (auth.uid() = owner);

drop policy if exists "Users can create their own books" on public.books;
create policy "Users can create their own books"
  on public.books for insert
  with check (auth.uid() = owner);

drop policy if exists "Users can update their own books" on public.books;
create policy "Users can update their own books"
  on public.books for update
  using (auth.uid() = owner)
  with check (auth.uid() = owner);

drop policy if exists "Users can delete their own books" on public.books;
create policy "Users can delete their own books"
  on public.books for delete
  using (auth.uid() = owner);
