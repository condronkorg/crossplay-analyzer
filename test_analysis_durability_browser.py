"""Auth changes preserve completed analysis; shared guests remain read-only."""
import unittest
from playwright.sync_api import expect
import test_review_browser as review_browser


class AnalysisDurabilityBrowserTest(unittest.TestCase):
    setUpClass=classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp=review_browser.ReviewBrowserTest.setUp
    tearDown=review_browser.ReviewBrowserTest.tearDown
    route_request=review_browser.ReviewBrowserTest.route_request
    connect=review_browser.ReviewBrowserTest.connect
    open_game=review_browser.ReviewBrowserTest.open_game
    start_review=review_browser.ReviewBrowserTest.start_review
    install_review_worker=review_browser.ReviewBrowserTest.install_review_worker

    def prepare_auth_change(self, fail=False):
        self.open_game()
        self.page.evaluate('''fail=>{
          persistenceEnabled=true;signedInUser={id:'synthetic-owner',email:'owner@example.test'};
          CrossplayAuth.getSession=()=>({user:signedInUser});
          CrossplayAuth.accessToken=async()=> 'synthetic-token';
          window.authOrder=[];window.savedAnalysis=[];window.originalContext=contextFor(currentReview);
          originalContext.cache.owner=analysisOwner();
          CrossplayAuth.signOut=async()=>authOrder.push('sign-out');
          CrossplayAuth.signInWithGoogle=async()=>authOrder.push('sign-in');
          const originalApi=api;
          api=async(path,options)=>{
            if(!path.endsWith('/analysis'))return originalApi(path,options);
            if(fail)throw new Error('Synthetic save outage');
            authOrder.push('save');savedAnalysis.push(JSON.parse(options.body));return {saved:true};
          };
          recordEngineResult(currentReview,positionCache,'synthetic-result',{simulated:true});
          markAnalysisComplete('move_quality',{state:'done',points:[]},positionCache);
          updateAuthUi();
        }''',fail)

    def test_full_result_is_saved_without_waiting_for_the_batch_timer(self):
        self.install_review_worker()
        self.open_game()
        result=self.page.evaluate("""async()=>{
          const context=contextFor(currentReview),turn=currentReview.turns[0];
          await analyzeReviewTurn(context,0,'review');
          await flushAnalysisSave(currentReview,context.cache);
          const key=context.cache.key(turn,'review'),full=context.cache.get(turn,'review');
          const writes=[],request=api;
          api=async(path,options)=>{
            if(path.endsWith('/analysis'))writes.push(JSON.parse(options.body));
            return request(path,options);
          };
          recordEngineResult(currentReview,context.cache,key,full);
          await analysisSaveQueue;
          clearTimeout(context.cache.saveTimer);
          return {writes,key,samples:full.iterationsCompleted,
            valid:CrossplayReview.validResult(full,turn,context.cache.seed(turn),277,15)};
        }""")
        self.assertTrue(result['valid'])
        self.assertEqual(result['samples'],277)
        self.assertEqual(len(result['writes']),1)
        self.assertEqual(result['writes'][0]['turn_analyses'][result['key']]['iterationsCompleted'],277)

    def test_sign_out_saves_raw_results_and_summary_before_revoking_auth(self):
        self.prepare_auth_change()
        self.page.get_by_role('button',name='Sign out',exact=True).click()
        expect(self.page.locator('#connectionState')).to_contain_text('Signed out')
        result=self.page.evaluate('({order:authOrder,saved:savedAnalysis,user:signedInUser})')
        self.assertEqual(result['order'],['save','sign-out'])
        self.assertIn('synthetic-result',result['saved'][0]['turn_analyses'])
        self.assertEqual(result['saved'][0]['move_quality']['state'],'done')
        self.assertIsNone(result['user'])

    def test_saved_owner_results_survive_sign_out_and_a_new_review_context(self):
        self.install_review_worker()
        self.open_game()
        self.page.evaluate("""async()=>{
          await analyzeReviewTurn(contextFor(currentReview),0,'review');
          window.savedFirstWin=positionCache.get(currentReview.turns[0],'review').selected.win;
          CrossplayAuth.signOut=async()=>{};
          byId('signOutButton').click();
        }""")
        expect(self.page.locator('#connectionState')).to_contain_text('Signed out')
        result=self.page.evaluate("""async()=>{
          CrossplayAuth.initialize=async()=>null;
          CrossplayAuth.getSession=()=>null;CrossplayAuth.accessToken=async()=>null;
          await start();await openGame('mockgame01');
          const restored=positionCache.get(currentReview.turns[0],'review');
          let repeated=0;
          workerRequest=async()=>{repeated++;throw new Error('Repeated completed analysis');};
          await analyzeReviewTurn(contextFor(currentReview),0,'review');
          return {restored:restored?.selected.win,expected:savedFirstWin,repeated,
            point:contextFor(currentReview).quality.points.find(point=>point.turnIndex===0)};
        }""")
        self.assertEqual(result['restored'],result['expected'])
        self.assertEqual(result['point']['playedWin'],result['expected'])
        self.assertEqual(result['repeated'],0)

    def test_save_failure_prevents_sign_out_and_preserves_pending_work(self):
        self.prepare_auth_change(fail=True)
        self.page.get_by_role('button',name='Sign out',exact=True).click()
        expect(self.page.locator('#connectionState')).to_contain_text('could not be saved')
        result=self.page.evaluate('''()=>({order:authOrder,user:signedInUser.id,
          pending:Object.keys(originalContext.cache.unsaved),
          completed:Object.keys(originalContext.cache.completed),
          retained:reviewQueue.contexts.get(originalContext.review.id)===originalContext,
          paused:originalContext.paused,concurrency:reviewQueue.concurrency})''')
        self.assertEqual(result['order'],[])
        self.assertEqual(result['user'],'synthetic-owner')
        self.assertEqual(result['pending'],['synthetic-result'])
        self.assertIn('move_quality',result['completed'])
        self.assertTrue(result['retained'])
        self.assertFalse(result['paused'])
        self.assertGreater(result['concurrency'],0)

    def test_failed_save_with_expired_session_preserves_unsaved_account_work(self):
        self.prepare_auth_change(fail=True)
        self.page.evaluate("""() => {
          const request = api;
          api = async (path, options) => {
            if (path.endsWith('/analysis')) CrossplayAuth.getSession = () => null;
            return request(path, options);
          };
        }""")
        self.page.get_by_role('button',name='Sign out',exact=True).click()
        self.page.wait_for_function("!byId('signOutButton').disabled")
        result=self.page.evaluate("""() => ({order:authOrder,user:signedInUser,
          session:CrossplayAuth.getSession(),
          retained:reviewQueue?.contexts.get(originalContext.review.id)===originalContext,
          reviewId:currentReview?.id,
          pending:Object.keys(originalContext.cache.unsaved || {}),
          completed:Object.keys(originalContext.cache.completed || {}),
          message:byId('connectionState').textContent})""")
        self.assertEqual(result['order'],[])
        self.assertIsNone(result['session'])
        self.assertTrue(result['retained'])
        self.assertEqual(result['user']['id'],'synthetic-owner')
        self.assertEqual(result['reviewId'],'mockgame01')
        self.assertIn('synthetic-result',result['pending'])
        self.assertIn('move_quality',result['completed'])
        self.assertIn('could not be saved',result['message'])

    def test_logout_transport_failure_does_not_resume_a_cleared_session(self):
        self.prepare_auth_change()
        self.page.evaluate("""() => {
          CrossplayAuth.signOut = async () => {
            authOrder.push('sign-out');
            CrossplayAuth.getSession = () => null;
            throw new Error('Synthetic logout transport failure');
          };
        }""")
        self.page.get_by_role('button',name='Sign out',exact=True).click()
        self.page.wait_for_function("authOrder.includes('sign-out') && !byId('signOutButton').disabled")
        result=self.page.evaluate("""() => ({order:authOrder,saved:savedAnalysis,
          user:signedInUser,queue:reviewQueue,review:currentReview,
          signOutHidden:byId('signOutButton').classList.contains('hidden')})""")
        self.assertEqual(result['order'],['save','sign-out'])
        self.assertIn('synthetic-result',result['saved'][0]['turn_analyses'])
        self.assertIsNone(result['user'])
        self.assertIsNone(result['queue'])
        self.assertIsNone(result['review'])
        self.assertTrue(result['signOutHidden'])

    def test_auth_change_waits_for_active_work_and_saves_its_result(self):
        self.prepare_auth_change()
        self.page.evaluate('''()=>{
          const queue=reviewQueue;queue.running=1;
          originalContext.cache.pending.set('running',{promise:new Promise(resolve=>{
            setTimeout(()=>{
              authOrder.push('worker-finish');
              recordEngineResult(originalContext.review,originalContext.cache,'late-result',{simulated:true});
              originalContext.cache.pending.delete('running');queue.running=0;resolve();
            },50);
          })});
        }''')
        self.page.get_by_role('button',name='Sign out',exact=True).click()
        expect(self.page.locator('#connectionState')).to_contain_text('Signed out')
        result=self.page.evaluate('({order:authOrder,saved:savedAnalysis})')
        self.assertLess(result['order'].index('worker-finish'),result['order'].index('sign-out'))
        self.assertIn('late-result',{key for body in result['saved'] for key in body['turn_analyses']})

    def test_google_redirect_saves_before_navigation(self):
        self.prepare_auth_change()
        self.page.evaluate("byId('googleSignInButton').click()")
        self.page.wait_for_function("authOrder.includes('sign-in')")
        self.assertEqual(self.page.evaluate('authOrder'),['save','sign-in'])

    def prepare_shared_start(self, owner=True, signed_in=True, auth_failure=False):
        self.open_game()
        self.page.evaluate('''({owner,signedIn,authFailure})=>{
          window.sharedReview=structuredClone(currentReview);
          history.replaceState({},'',gameShareUrl(sharedReview.share_token));
          clearAccountUi('Synthetic restart');
          persistenceEnabled=false;signedInUser=null;window.sharedCalls=[];
          const user={id:'synthetic-shared-user',email:'viewer@example.test'};
          CrossplayAuth.initialize=async()=>{sharedCalls.push('initialize');if(authFailure)throw new Error('Synthetic auth outage');};
          CrossplayAuth.getSession=()=>signedIn&&!authFailure?{user}:null;
          CrossplayAuth.accessToken=async()=>signedIn&&!authFailure?'synthetic-shared-token':null;
          api=async(path,options={})=>{
            sharedCalls.push(path);
            if(path.startsWith('shared/'))return structuredClone(sharedReview);
            if(path==='status')return {auth_required:true,authenticated:signedIn,configured:false,email:user.email};
            if(path==='games/'+sharedReview.id+'/review')return {...structuredClone(sharedReview),
              share_token:owner?sharedReview.share_token:'different-private-copy-token'};
            if(path==='games')return {games:[]};
            if(path.startsWith('games?'))return {games:[],next_cursor:null};
            if(path.endsWith('/analysis'))return {saved:true};
            throw new Error('Unexpected synthetic request '+path);
          };
          startBackgroundAnalysis=()=>{};
        }''',{'owner':owner,'signedIn':signed_in,'authFailure':auth_failure})

    def test_signed_in_shared_owner_uses_private_review_and_can_save(self):
        self.prepare_shared_start()
        self.page.evaluate('start()')
        result=self.page.evaluate('''async()=>{
          recordEngineResult(currentReview,positionCache,'synthetic-owner-result',{simulated:true});
          await flushAnalysisSave();
          return {shared:viewingSharedGame,cacheShared:positionCache.shared,calls:sharedCalls,url:location.search};
        }''')
        self.assertFalse(result['shared']);self.assertFalse(result['cacheShared'])
        self.assertEqual(result['calls'][0],'initialize')
        self.assertIn('?review=',result['url'])
        self.assertEqual(sum(path.startswith('shared/') for path in result['calls']),1)
        self.assertTrue(any(path.endswith('/analysis') for path in result['calls']))

    def test_signed_in_non_owner_stays_guest_and_never_loads_catalog_or_saves(self):
        self.prepare_shared_start(owner=False)
        self.page.evaluate('start()')
        result=self.page.evaluate('''async()=>{
          recordEngineResult(currentReview,positionCache,'synthetic-guest-result',{simulated:true});
          await flushAnalysisSave();
          return {shared:viewingSharedGame,cacheShared:positionCache.shared,calls:sharedCalls,url:location.search};
        }''')
        self.assertTrue(result['shared']);self.assertTrue(result['cacheShared'])
        self.assertIn('?game=',result['url'])
        self.assertFalse(any(path=='games' or path.startswith('games?') or path.endswith('/analysis') for path in result['calls']))

    def test_shared_guest_is_not_blocked_by_stalled_optional_auth(self):
        self.prepare_shared_start()
        self.page.evaluate("CrossplayAuth.initialize=()=>new Promise(()=>{}); undefined")
        self.page.evaluate('start()')
        result=self.page.evaluate('({shared:viewingSharedGame,calls:sharedCalls,loaded:!!currentReview})')
        self.assertTrue(result['shared']);self.assertTrue(result['loaded'])
        self.assertEqual(len(result['calls']),1)
        self.assertTrue(result['calls'][0].startswith('shared/'))

    def test_anonymous_shared_game_works_when_auth_initialization_fails(self):
        self.prepare_shared_start(signed_in=False,auth_failure=True)
        self.page.evaluate('start()')
        result=self.page.evaluate('({shared:viewingSharedGame,calls:sharedCalls,loaded:!!currentReview})')
        self.assertTrue(result['shared']);self.assertTrue(result['loaded'])
        self.assertEqual(len(result['calls']),2)
        self.assertEqual(result['calls'][0],'initialize')
        self.assertTrue(result['calls'][1].startswith('shared/'))


if __name__=='__main__':unittest.main()
