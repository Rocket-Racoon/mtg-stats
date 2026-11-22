import { useEffect, type CSSProperties } from 'react';
import { Link, useParams } from 'react-router';
import {
  ApiError,
  usePlaygroup,
  usePlaygroupPlayers,
  type PlaygroupPlayer,
} from '@mtg-stats/api-client';
import { errorMessage, errorRequestId } from '../errors/message';
import { ActionSlot } from '../playgroups/ActionSlot';
import { useActivePlaygroupStore } from '../playgroups/active-store';
import { groupRoster, readableTextColor } from '../playgroups/roster';
import { useFetchAllPages } from '../playgroups/useAllPages';
import { NotFoundPage } from './NotFound';

const ROLE_LABEL = { owner: 'Owner', admin: 'Admin', member: 'Member' } as const;

function roleLabel(player: PlaygroupPlayer): string {
  if (player.is_guest) return 'Guest';
  return player.role ? ROLE_LABEL[player.role] : ROLE_LABEL.member;
}

/** The avatar colour, plus the initial. The role is always written out, never colour alone. */
function Avatar({ player }: { player: PlaygroupPlayer }) {
  const text = readableTextColor(player.avatar_color);
  const style: CSSProperties | undefined = text
    ? { backgroundColor: player.avatar_color, color: text }
    : undefined;
  return (
    <span className="avatar" style={style} aria-hidden="true">
      {[...player.display_name][0]?.toUpperCase() ?? '?'}
    </span>
  );
}

function PeopleList({
  title,
  headingId,
  players,
}: {
  title: string;
  headingId: string;
  players: readonly PlaygroupPlayer[];
}) {
  if (players.length === 0) return null;
  return (
    <section aria-labelledby={headingId}>
      <h4 id={headingId}>{title}</h4>
      <ul className="people-list">
        {players.map((player) => (
          <li key={player.id}>
            <Avatar player={player} />
            <span>{player.display_name}</span>
            <span className="people-role"> ({roleLabel(player)})</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function PlayersError({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <p role="alert">
      {errorMessage(error)}{' '}
      <button type="button" onClick={retry}>
        Retry
      </button>
    </p>
  );
}

/**
 * `/playgroups/:playgroupId`: a playgroup's home (S-2.4.5). Opening it makes that playgroup the
 * active one. Shows the name, the people (owner, admins, members, then guests, every page of the
 * roster) and the capability slots the caller's role allows. A playgroup the caller is not in,
 * or an unknown id, is the app's not-found page (the API answers both with the same 404).
 */
export function PlaygroupHomePage() {
  const { playgroupId = '' } = useParams();
  const playgroup = usePlaygroup(playgroupId);
  const players = usePlaygroupPlayers(playgroupId);
  useFetchAllPages(players);
  const setActive = useActivePlaygroupStore((s) => s.setActive);
  const loadedId = playgroup.data?.id;

  useEffect(() => {
    if (loadedId) setActive(loadedId); // a deep link to one of my playgroups makes it active
  }, [loadedId, setActive]);

  const name = playgroup.data?.name;
  useEffect(() => {
    document.title = `${name ?? 'Playgroup'} · MTG Stats`;
  }, [name]);

  if (playgroup.isError) {
    const { error } = playgroup;
    if (error instanceof ApiError && error.code === 'not_found') return <NotFoundPage />;
    const requestId = errorRequestId(error);
    return (
      <section aria-labelledby="page-title">
        <h2 id="page-title">Playgroup</h2>
        <p role="alert">
          {errorMessage(error)}
          {requestId ? ` (request ${requestId})` : ''}{' '}
          <button
            type="button"
            onClick={() => {
              void playgroup.refetch();
            }}
          >
            Retry
          </button>
        </p>
      </section>
    );
  }

  if (playgroup.isPending) {
    return (
      <section aria-labelledby="page-title">
        <h2 id="page-title">Playgroup</h2>
        <p role="status">Loading playgroup…</p>
      </section>
    );
  }

  const { data } = playgroup;
  const role = data.role;
  const roster = groupRoster(players.data?.pages.flatMap((page) => page.items) ?? []);
  const rosterReady = players.isSuccess && !players.hasNextPage;

  return (
    <section aria-labelledby="page-title">
      <h2 id="page-title">{data.name}</h2>
      {data.description ? <p>{data.description}</p> : null}
      <p>
        Your role: <strong>{ROLE_LABEL[role]}</strong>
      </p>

      <section aria-labelledby="people-title">
        <h3 id="people-title">People</h3>
        {players.isError ? (
          <PlayersError
            error={players.error}
            retry={() => {
              void players.refetch();
            }}
          />
        ) : rosterReady ? (
          <>
            <PeopleList title="Owner" headingId="owner-title" players={roster.owners} />
            <PeopleList title="Admins" headingId="admins-title" players={roster.admins} />
            <PeopleList title="Members" headingId="members-title" players={roster.members} />
            <PeopleList title="Guests" headingId="guests-title" players={roster.guests} />
          </>
        ) : (
          <p role="status">Loading people…</p>
        )}
      </section>

      <section aria-labelledby="actions-title">
        <h3 id="actions-title">Manage</h3>
        <p>
          <Link to="/playgroups">All your playgroups</Link>
        </p>
        <ActionSlot action="invites.manage" role={role} title="Invite people" />
        <ActionSlot action="playgroup.edit" role={role} title="Name and description" />
        <ActionSlot action="houseRules.edit" role={role} title="House rules" />
        <ActionSlot action="guests.add" role={role} title="Manage guests" />
        <ActionSlot action="members.changeRole" role={role} title="Admins and members" />
        <ActionSlot action="ownership.transfer" role={role} title="Transfer ownership" />
      </section>
    </section>
  );
}
