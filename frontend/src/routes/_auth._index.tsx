import { useEffect } from "react";
import { useNavigate } from "react-router";
import { MessageSquarePlus, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useCreateSession, useSessions } from "@/lib/queries";

export default function IndexRoute() {
  const navigate = useNavigate();
  const { data: sessions, isLoading } = useSessions();
  const createSession = useCreateSession();

  // Redirect to the most recent session once sessions load.
  useEffect(() => {
    if (!isLoading && sessions && sessions.length > 0) {
      navigate(`/chat/${sessions[0].id}`, { replace: true });
    }
  }, [isLoading, sessions, navigate]);

  const handleNewChat = async () => {
    const session = await createSession.mutateAsync();
    navigate(`/chat/${session.id}`);
  };

  if (isLoading || (sessions && sessions.length > 0)) {
    return <div className="flex-1" />;
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="flex size-14 items-center justify-center rounded-2xl bg-brand-blue/10">
        <Sparkles className="size-7 text-brand-blue" />
      </div>
      <div>
        <h1 className="text-2xl font-semibold">Welcome to CityA</h1>
        <p className="mt-1 max-w-md text-muted-foreground">
          Start a conversation grounded in your knowledge base.
        </p>
      </div>
      <Button onClick={handleNewChat} size="lg">
        <MessageSquarePlus className="size-5" />
        New chat
      </Button>
    </div>
  );
}
