import { Redirect } from 'expo-router';
import { useSession } from '../session/SessionProvider';

/** The root URL lands on the first tab when signed in, otherwise on the sign-in stack. */
export default function Index() {
  const { status } = useSession();
  if (status === 'restoring') return null;
  return <Redirect href={status === 'signed-in' ? '/play' : '/sign-in'} />;
}
