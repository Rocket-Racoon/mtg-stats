import { render, screen } from '@testing-library/react-native';
import Index from '../app/index';

describe('Index', () => {
  it('renders the shell with the linked workspace packages', async () => {
    await render(<Index />);
    expect(screen.getByText('MTG Stats')).toBeTruthy();
    expect(screen.getByTestId('linked-packages')).toBeTruthy();
  });
});
